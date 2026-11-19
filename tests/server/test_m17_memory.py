"""Stage 4h: per-chat dialog memory — storage, prompt block, background extraction, API."""
import json
import secrets
import time
import uuid

import pytest

from server.ego_core import memory as mem
from server.ego_core.memory import ChatMemory, parse_facts
from server.ego_core.roleplay_prompt import compile_messages
from server.ego_core.service import CoreFailure
from server.ego_core.store import MainStore
from test_core_api import LocalClient, core, new_chat, uid, wait_terminal  # noqa: F401  (fixture reuse)

KEY = secrets.token_bytes(32)


@pytest.fixture
def mstore(tmp_path):
    store = MainStore(tmp_path / 'main.db', KEY)
    yield store
    store.close()


def _versioned_chat(store, n):
    """A chat with n user/assistant exchanges (2n messages)."""
    chat = store.create_chat(str(uuid.uuid4()), 'память')
    for i in range(n):
        current = [c for c in store.list_chats()['chats'] if c['id'] == chat['id']][0] \
            if isinstance(store.list_chats(), dict) else [c for c in store.list_chats() if c['id'] == chat['id']][0]
        generation = store.begin_generation(str(uuid.uuid4()), chat['id'], f'Реплика {i}', current['version'])
        store.set_generation_state(generation['id'], 'started')
        store.set_generation_state(generation['id'], 'completed', text=f'Ответ {i}')
    return chat['id']


def test_parse_facts_accepts_json_or_bullets_and_drops_blocked(monkeypatch):
    import server.ego_core.content_guard as cg; monkeypatch.setattr(cg, 'NONCONSENT_FILTER', True)
    assert parse_facts('Вот: ["Пользователя зовут Алекс", "Лира боится воды", 5]') == ['Пользователя зовут Алекс', 'Лира боится воды']
    assert parse_facts('- Торн хромает\n2) Встреча в библиотеке') == ['Торн хромает', 'Встреча в библиотеке']
    assert parse_facts('["Её изнасиловали"]') == []          # reply-side hard limit
    assert parse_facts('["У Торна двое детей"]') == ['У Торна двое детей']   # non-sexual mention stays
    assert len(parse_facts(json.dumps([f'Факт номер {i}' for i in range(20)]))) == mem.FACTS_PER_RUN


def test_manual_items_pin_edit_delete_and_isolation(mstore):
    memory = ChatMemory(mstore)
    a, b = _versioned_chat(mstore, 1), _versioned_chat(mstore, 1)
    view = memory.add(a, '  Пользователя зовут Алекс ', pinned=True)
    item = view['items'][0]
    assert item['text'] == 'Пользователя зовут Алекс' and item['pinned'] and item['kind'] == 'manual' and item['edited_by'] == 'user'
    assert memory.view(b)['items'] == []                      # never crosses chats
    with pytest.raises(CoreFailure, match='NOT_FOUND'):
        memory.update(b, item['id'], pinned=False)
    memory.update(a, item['id'], text='Пользователя зовут Алекс, ему 30', pinned=False)
    assert memory.view(a)['items'][0]['pinned'] is False
    with pytest.raises(CoreFailure, match='CONTENT_BLOCKED'):
        memory.add(a, 'Она школьница, он её целует')         # same rule as chat input
    with pytest.raises(CoreFailure, match='NOT_FOUND'):
        memory.add(str(uuid.uuid4()), 'нет такого чата')
    memory.delete(a, item['id'])
    assert memory.view(a)['items'] == []


def test_due_extraction_dedup_and_settings(mstore):
    memory = ChatMemory(mstore)
    chat = _versioned_chat(mstore, 4)                         # 8 messages < 10
    assert not memory.due(chat)
    memory.settings(chat, interval=8)
    assert memory.due(chat)
    messages, first, last = memory.extraction_request(chat)
    assert messages[0]['content'] == mem.extract_system() and 'Реплика 3' in messages[1]['content']
    assert memory.apply_extraction(chat, '["Торн боится воды", "торн боится воды!"]', first, last) == 1
    assert not memory.due(chat) and memory.view(chat)['new_messages'] == 0
    memory.settings(chat, enabled=False)
    assert memory.context(chat) == ''                         # disabled memory is not injected
    with pytest.raises(CoreFailure, match='INVALID_INPUT'):
        memory.settings(chat, interval=2)


def test_context_block_respects_budget_pins_and_order(mstore):
    memory = ChatMemory(mstore)
    chat = _versioned_chat(mstore, 1)
    memory.add(chat, 'Закреплённый факт', pinned=True)
    for i in range(60):
        memory.add(chat, f'Факт номер {i} ' + 'подробности ' * 3)
    block = memory.context(chat, window=8192)
    assert block.startswith('Память этой истории') and 'Закреплённый факт' in block
    assert 'Факт номер 59' in block and 'Факт номер 0 ' not in block    # newest kept, oldest dropped
    assert mem.estimate_tokens(block) <= mem.CONTEXT_FACTS + 120
    assert block.index('Закреплённый факт') < block.index('Факт номер 59')
    small = memory.context(chat, window=4096)
    assert len(small) < len(block)
    system = compile_messages([{'role': 'user', 'content': 'Привет', 'status': 'completed'}],
                                   character_name='Торн', character_description='Странник', memory=block)[0]['content']
    assert system.endswith(block)


def test_compression_folds_oldest_auto_facts(mstore):
    memory = ChatMemory(mstore)
    chat = _versioned_chat(mstore, 2)
    messages, first, last = memory.extraction_request(chat)
    import hashlib
    words = ['река', 'город', 'меч', 'сестра', 'башня', 'лес', 'обет', 'карта', 'волк', 'песня', 'корабль', 'ключ']
    def fact(n):      # distinct facts (near-duplicates are merged since 2026-11-06)
        h = hashlib.md5(str(n).encode()).digest()
        return f'Событие {n}: ' + ' '.join(words[b % len(words)] + str(b) for b in h[:10]) + ' и долгое описание пути'
    for run in range(5):
        facts = json.dumps([fact(run * 8 + i) for i in range(8)], ensure_ascii=False)
        memory.apply_extraction(chat, facts, first, last)
    memory.add(chat, 'Ручной факт пользователя')
    request = memory.summary_request(chat)
    assert request is not None
    messages, archived, through = request
    assert 'Ручной факт' not in messages[1]['content']        # user items are never folded
    assert memory.apply_summary(chat, 'Конспект: Торн и Тест прошли долгий путь вместе.', archived, through)
    view = memory.view(chat)
    assert view['items'][0]['kind'] == 'summary' and view['archived_count'] == len(archived)
    assert any(i['text'] == 'Ручной факт пользователя' for i in view['items'])
    assert memory.summary_request(chat) is None                # not again before 50 new messages
    assert 'Конспект прошлых событий' in memory.context(chat)


# ---- integration with the real service + fake llama-server ------------------------------
def _enable_fake_log(runtime, monkeypatch, tmp_path, **extra):
    log = tmp_path / 'chat-requests.jsonl'
    original = runtime.child_environment
    monkeypatch.setattr(runtime, 'child_environment', lambda: {**original(), 'EGO_FAKE_CHAT_LOG': str(log), **extra})
    return log


def _say(c, chat, text):
    version = [x for x in c.get('/v1/chats').json()['chats'] if x['id'] == chat['id']][0]['version'] \
        if isinstance(c.get('/v1/chats').json(), dict) else [x for x in c.get('/v1/chats').json() if x['id'] == chat['id']][0]['version']
    r = c.post('/v1/generations', json={'operation_id': uid(), 'chat_id': chat['id'], 'content': text, 'expected_version': version,
                                        'character_name': 'Торн', 'character_description': 'Странник с севера.'})
    assert r.status_code == 202, r.text
    assert wait_terminal(c, r.json()['generation']['id'])['state'] == 'completed'


def _wait_memory_idle(service):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if service.status()['memory_job'] is None:
            return
        time.sleep(.02)
    pytest.fail('memory job did not finish')


def test_service_extracts_after_interval_and_injects_into_prompt(core, monkeypatch, tmp_path):
    c, service, store, runtime, _ = core
    log = _enable_fake_log(runtime, monkeypatch, tmp_path)
    chat = new_chat(c, 'память')
    assert c.put(f"/v1/chats/{chat['id']}/memory/settings", json={'interval': 4}).status_code == 200
    _say(c, chat, 'Привет, я Тест')
    _wait_memory_idle(service)
    assert c.get(f"/v1/chats/{chat['id']}/memory").json()['items'] == []       # 2 messages < 4
    _say(c, chat, 'Пойдём к реке')
    _wait_memory_idle(service)
    view = c.get(f"/v1/chats/{chat['id']}/memory").json()
    assert [i['text'] for i in view['items']] == ['Пользователя зовут Тест', 'Торн боится воды']
    assert view['new_messages'] == 0 and view['last_result'] == 'facts:2'
    assert service.status()['runtime_state'] == 'READY'
    _say(c, chat, 'Что дальше?')
    requests = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
    system = requests[-1][0]['content']
    assert 'Торн боится воды' in system and 'Память этой истории' in system
    other = new_chat(c, 'другой')
    _say(c, other, 'Привет')
    assert 'Торн боится воды' not in json.loads(log.read_text(encoding='utf-8').splitlines()[-1])[0]['content']


def test_user_reply_cancels_background_memory_job(core, monkeypatch, tmp_path):
    c, service, store, runtime, _ = core
    _enable_fake_log(runtime, monkeypatch, tmp_path, EGO_FAKE_SLOW_MEMORY='1')
    chat = new_chat(c, 'отмена')
    c.put(f"/v1/chats/{chat['id']}/memory/settings", json={'interval': 4})
    _say(c, chat, 'Раз')
    _say(c, chat, 'Два')                                       # starts a slow (~9 s) memory job
    deadline = time.monotonic() + 3
    while service.status()['memory_job'] is None and time.monotonic() < deadline:
        time.sleep(.01)
    assert service.status()['memory_job'] == chat['id']
    started = time.monotonic()
    _say(c, chat, 'Три')                                       # wins over memory
    assert time.monotonic() - started < 6                      # did not wait for the 9 s job
    assert c.get(f"/v1/chats/{chat['id']}/memory").json()['items'] == []   # cancelled job saved nothing


def test_memory_api_validation_and_manual_extract(core, monkeypatch, tmp_path):
    c, service, store, runtime, _ = core
    _enable_fake_log(runtime, monkeypatch, tmp_path)
    assert c.get('/v1/chats/not-a-uuid/memory').json()['code'] == 'INVALID_ID'
    assert c.get(f'/v1/chats/{uid()}/memory').status_code == 404
    chat = new_chat(c, 'api')
    r = c.post(f"/v1/chats/{chat['id']}/memory", json={'text': 'Тест любит чай', 'pinned': True})
    assert r.status_code == 200 and r.json()['items'][0]['pinned'] is True
    item = r.json()['items'][0]['id']
    assert c.put(f"/v1/chats/{chat['id']}/memory/{item}", json={'text': 'Тест любит зелёный чай'}).json()['items'][0]['text'] == 'Тест любит зелёный чай'
    assert c.post(f"/v1/chats/{chat['id']}/memory", json={'text': 'x' * 301}).status_code == 422
    _say(c, chat, 'Привет')
    _wait_memory_idle(service)
    assert c.post(f"/v1/chats/{chat['id']}/memory/extract").status_code == 202
    _wait_memory_idle(service)
    texts = [i['text'] for i in c.get(f"/v1/chats/{chat['id']}/memory").json()['items']]
    assert 'Торн боится воды' in texts and 'Тест любит зелёный чай' in texts
    assert c.request('DELETE', f"/v1/chats/{chat['id']}/memory/{item}").status_code == 200


def test_guesses_are_not_kept_as_facts():
    facts = parse_facts('["Ливий ищет книгу о Мервине", "Ключ может быть связан с Мервином", "Лира может помочь Ливию", "Возможно, сестра жива"]')
    assert facts == ['Ливий ищет книгу о Мервине']


def test_odd_facts_and_near_duplicates_are_not_kept(mstore):
    assert parse_facts('["Кира может предложить все fours", "Кира чувствует твоё прикосновение", "Кира держит бар у доков"]') == ['Кира держит бар у доков']
    memory = ChatMemory(mstore)
    chat = mstore.create_chat(str(uuid.uuid4()), 'dups')['id']
    assert memory.apply_extraction(chat, '["Кира хочет, чтобы Пользователь танцевал с ней в баре"]', 1, 2) == 1
    assert memory.apply_extraction(chat, '["Кира хочет, чтобы Пользователь танцевал с нею в баре", "Кира любит свою работу"]', 3, 4) == 1


def test_extraction_prompt_has_no_example_facts():
    assert 'моряк' not in mem.EXTRACT_SYSTEM and 'темноты' not in mem.EXTRACT_SYSTEM and 'Мира' not in mem.EXTRACT_SYSTEM
