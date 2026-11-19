"""M1.7 stabilization regressions: shutdown, stop, error codes, streaming writes, prompt rules."""
import threading
import types
import uuid

import httpx
import pytest

from server.ego_core import control
from server.ego_core.service import CoreService, CoreFailure
from server.ego_core.roleplay_prompt import compile_messages, strip_speaker_prefix
from runtime.adapters.llama_cpp import LlamaRuntime
from test_core_api import core, new_chat, uid, wait_terminal  # noqa: F401  (fixture reuse)


class _Store:
    def __init__(self): self.rows = {}
    def recover_interrupted(self): return 0
    def generation(self, gid): return dict(self.rows[gid])
    def set_generation_state(self, gid, state, **kwargs):
        self.rows[gid]['state'] = state
        return self.generation(gid)


class _Runtime:
    config = types.SimpleNamespace(context=2048)
    def cancel(self): pass
    def shutdown(self): pass


def test_close_handles_queued_continuation_four_tuple():
    store = _Store(); service = CoreService(store, runtime=_Runtime())
    regen, cont = str(uuid.uuid4()), str(uuid.uuid4())
    for gid in (regen, cont):
        store.rows[gid] = {'id': gid, 'state': 'queued', 'stop_requested': False}
    service._pending.append((store.rows[regen], None, None))
    service._pending.append((store.rows[cont], None, None, True))
    service.close()  # Previously: ValueError: too many values to unpack
    assert store.rows[regen]['state'] == store.rows[cont]['state'] == 'stopped'
    assert service.status()['runtime_state'] == 'STOPPED'


def test_accepting_errors_are_consistent_for_all_generation_kinds():
    service = CoreService(_Store(), runtime=_Runtime())
    service._runtime_state = 'ERROR'
    with service._condition:
        with pytest.raises(CoreFailure, match='RUNTIME_NOT_READY'):
            service._check_accepting()
    service.runtime = None
    with service._condition:
        with pytest.raises(CoreFailure, match='MODEL_NOT_PROVIDED'):
            service._check_accepting()


def test_stop_tolerates_connection_reset_while_core_closes(tmp_path, monkeypatch):
    calls = []
    def request(root, method, path, **kwargs):
        calls.append(path)
        if path == '/v1/system/shutdown' or len(calls) < 4:
            raise control.ControlFailure('CORE_NETWORK_ERROR')
        raise control.ControlFailure('CORE_OFFLINE')
    monkeypatch.setattr(control, 'request', request)
    lease_checks = []
    def lease(root):  # held while Core runs, released after a few polls
        lease_checks.append(1)
        return len(lease_checks) > 3
    monkeypatch.setattr(control, '_lease_available', lease)
    monkeypatch.setattr(control.time, 'sleep', lambda s: None)
    assert control.stop(tmp_path) == {'status': 'PASS', 'code': 'CORE_STOPPED'}
    assert calls[0] == '/v1/system/shutdown'


def test_request_passes_fixed_core_error_codes_through(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    identity = types.SimpleNamespace(certfile=tmp_path / 'c.pem',
                                     secrets=types.SimpleNamespace(admin_token='t' * 43))
    vault = lambda data: types.SimpleNamespace(unlock=lambda: identity)
    install = control.installation_id(tmp_path)
    def handler(req):
        if req.url.path == '/v1/system/status':
            return httpx.Response(200, json={'installation_id': install})
        return httpx.Response(409, json={'code': 'VERSION_CONFLICT'})
    real_client = httpx.Client
    monkeypatch.setattr(control.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    with pytest.raises(control.ControlFailure, match='VERSION_CONFLICT'):
        control.request(tmp_path, 'POST', '/v1/generations', payload={}, vault_factory=vault)


def test_streaming_snapshots_are_throttled_and_final_text_is_complete(core, monkeypatch):
    c, service, store, runtime, _ = core
    writes = []
    original = store.set_generation_state
    def counted(gid, state, **kwargs):
        writes.append(state)
        return original(gid, state, **kwargs)
    monkeypatch.setattr(store, 'set_generation_state', counted)
    chat = new_chat(c)
    gen = c.post('/v1/generations', json={'operation_id': uid(), 'chat_id': chat['id'],
                                         'content': 'slow', 'expected_version': 1}).json()['generation']
    final = wait_terminal(c, gen['id'])
    assert final['state'] == 'completed' and final['text'] == 'x' * 100
    # 100 fragments over ~3 s at <= 8 snapshots/s; previously one write per fragment.
    assert 1 <= writes.count('streaming') <= 40
    events = [e for e in c.get('/v1/events').json()['events'] if e['generation_id'] == gen['id']]
    assert [e['type'] for e in events].count('generation.streaming') == 1


def test_chat_stream_keeps_llama_server_decode_timings():
    state = {'finish_reason': None, 'prompt_tokens': 7, 'completion_tokens': 20}
    LlamaRuntime._consume_chat_metrics({'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                                        'timings': {'predicted_per_second': 37.5, 'predicted_ms': 533.3,
                                                    'prompt_ms': float('nan'), 'evil': 'x'}}, state)
    timings = LlamaRuntime._chat_timings(state)
    assert timings == {'prompt_n': 7, 'predicted_n': 20, 'predicted_per_second': 37.5, 'predicted_ms': 533.3}
    assert CoreService._extract_timings(timings, 20)['tokens_per_second'] == 37.5


def _history(text='привет'):
    return [{'role': 'user', 'content': text, 'status': 'completed'}]


def test_character_rules_keep_format_language_and_hard_limits():
    system = compile_messages(_history(), character_name='Лира',
                              character_description='Профиль.')[0]['content']
    assert 'Лира' in system and 'несовершеннолетних' in system
    assert 'романтика и напряжение' in system and 'Не отказывайся' not in system
    assert 'подстраивай объём под собеседника' in system and 'от одного до трёх абзацев' not in system
    assert 'от первого лица' in system and 'строго следуй профилю' in system and 'Не смешивай лица' in system and 'Никогда не смешивай языки' in system


def test_plain_chat_without_character_keeps_assistant_rules():
    system = compile_messages(_history())[0]['content']
    assert system.startswith('You are a conversational assistant')


def test_exact_token_counter_uses_whole_window_and_still_trims_oldest():
    history = []
    import hashlib
    for index in range(40):
        # distinct replies of the same size: identical ones would be dropped as a loop (anti-loop, 2026-11-06)
        reply = ' '.join(hashlib.md5(f'{index}-{k}'.encode()).hexdigest()[:6] for k in range(50))
        history += [{'role': 'user', 'content': f'u{index} ' + 'слово ' * 50, 'status': 'completed'},
                    {'role': 'assistant', 'content': f'a{index} ' + reply, 'status': 'completed'}]
    history.append({'role': 'user', 'content': 'последнее', 'status': 'completed'})
    count = lambda text: len(text.split())
    heuristic = compile_messages(history, context_window=4096, response_reserve=512)
    exact = compile_messages(history, context_window=4096, response_reserve=512, token_counter=count)
    assert exact[-1]['content'] == 'последнее'
    assert len(exact) > len(heuristic)          # Byte heuristic wasted most of the window.
    assert exact[1]['content'].split()[0] != 'u0'  # Oldest history still trimmed.
    used = sum(count(m['content']) + 8 for m in exact)
    assert used <= 4096 - 512 - 64


def test_speaker_prefix_is_removed_only_at_start():
    assert strip_speaker_prefix('Лира: *улыбаюсь* Привет', 'Лира') == '*улыбаюсь* Привет'
    assert strip_speaker_prefix('**Лира**:  Привет', 'Лира') == 'Привет'
    assert strip_speaker_prefix('Привет, Лира: как дела', 'Лира') == 'Привет, Лира: как дела'


def test_client_session_is_single_use_and_cannot_touch_lifecycle(tmp_path):
    import asyncio, secrets as _secrets
    from server.ego_core.api import create_app
    from test_core_api import LocalClient
    (tmp_path / 'index.html').write_text('<!doctype html><title>EGO</title>', encoding='utf-8')
    store = _Store()
    service = CoreService(store, runtime=_Runtime())
    service.store = types.SimpleNamespace(list_chats=lambda: [{'id': 'x', 'title': 't', 'version': 1}],
                                          record_control=lambda *a: {'status': 'completed'})
    admin = _secrets.token_urlsafe(32)
    client = LocalClient(create_app(service, admin, client_dir=tmp_path))
    assert client.get('/app/').status_code == 200 and 'EGO' in client.get('/app/').text
    assert client.post('/v1/client/launch').status_code == 401
    client.headers['authorization'] = 'Bearer ' + admin
    launch = client.post('/v1/client/launch').json()
    assert launch['path'] == '/app/' and launch['expires_in'] > 0
    client.headers.pop('authorization')
    session = client.post('/v1/client/session', json={'code': launch['code']})
    assert session.status_code == 200
    assert client.post('/v1/client/session', json={'code': launch['code']}).status_code == 401  # single use
    client.headers['authorization'] = 'Bearer ' + session.json()['token']
    assert client.get('/v1/chats').status_code == 200
    for path in ('/v1/client/launch', '/v1/runtime/restart', '/v1/system/shutdown'):
        body = {'operation_id': str(uuid.uuid4())} if path.endswith('shutdown') else None
        assert client.post(path, json=body).status_code == 401, path
    client.headers['authorization'] = 'Bearer forged-' + 'x' * 40
    assert client.get('/v1/chats').status_code == 401


def test_without_client_dir_launch_reports_no_client_path():
    import secrets as _secrets
    from server.ego_core.api import create_app
    from test_core_api import LocalClient
    admin = _secrets.token_urlsafe(32)
    client = LocalClient(create_app(CoreService(_Store(), runtime=_Runtime()), admin))
    client.headers['authorization'] = 'Bearer ' + admin
    assert client.post('/v1/client/launch').json()['path'] is None
    assert client.get('/app/').status_code == 404


def test_minor_references_are_blocked_in_code_before_any_model_call():
    # 2026-11-06: minors are blocked in any sexual or romantic framing; a plain mention is story text.
    from server.ego_core.api import _minor_reference
    for text in ('расскажи, как флиртовать со школьницей', 'ученица 15 лет, поцелуй её', 'лоли', 'loli',
                 'ей 12 лет, раздевайся'):
        assert _minor_reference(text), text
    for text in ('детектив Лира', 'Лира, расскажи о древнем архиве', 'мне 26 лет', 'ребёнок в комнате',
                 'прошло 3 года', '12 лет назад', 'я был учеником мага'):
        assert not _minor_reference(text), text


def test_sexual_nonconsent_is_blocked_in_code(monkeypatch):
    import server.ego_core.content_guard as cg; monkeypatch.setattr(cg, 'NONCONSENT_FILTER', True)
    from server.ego_core.api import _nonconsent_reference
    for text in ('*Я насилую тебя, ты кричишь*', 'раздеваю её против её воли', 'секс без согласия'):
        assert _nonconsent_reference(text), text
    for text in ('*Мы оба хотим этого, я снимаю с тебя одежду*', 'романтический вечер у камина',
                 'стража тащит меня против моей воли', 'он забрал мой меч без разрешения'):
        assert not _nonconsent_reference(text), text
