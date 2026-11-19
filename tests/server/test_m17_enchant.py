"""2026-11-06: «Enchant your prompt» — one card field improved by the local model, on request."""
import json

import pytest

from server.ego_core import enchant
from server.ego_core.service import CoreFailure
from test_core_api import LocalClient, core, new_chat, uid, wait_terminal  # noqa: F401  (fixture reuse)


def _warm(c, runtime, monkeypatch, tmp_path):
    """One ordinary reply loads the fake model (READY) and turns on the request log."""
    log = tmp_path / 'requests.jsonl'
    original = runtime.child_environment
    monkeypatch.setattr(runtime, 'child_environment', lambda: {**original(), 'EGO_FAKE_CHAT_LOG': str(log)})
    chat = new_chat(c, 'разогрев')
    r = c.post('/v1/generations', json={'operation_id': uid(), 'chat_id': chat['id'], 'content': 'Привет', 'expected_version': chat['version'],
                                        'character_name': 'Торн', 'character_description': 'Странник.'})
    assert wait_terminal(c, r.json()['generation']['id'])['state'] == 'completed'
    return log


def test_request_uses_other_fields_and_rules():
    messages = enchant.request('world', 'description', 'Порт в тумане.',
                               {'name': 'Элдмур', 'canon': '— Магия запрещена', 'lore': '', 'unknown': 'игнор'})
    system, user = messages[0]['content'], messages[1]['content']
    assert 'улучшить поле «Описание мира»' in system and 'совершеннолетние' in system and 'школьной' in system
    assert 'Правила строгого канона: — Магия запрещена' in user and 'Имя: Элдмур' in user and 'игнор' not in user
    assert 'Порт в тумане.' in user
    empty = enchant.request('character', 'greeting', '', {})
    assert 'написать поле «Первое сообщение персонажа»' in empty[0]['content'] and 'пока пустое' in empty[1]['content']
    with pytest.raises(CoreFailure) as e:
        enchant.request('character', 'personality', 'Она школьница', {})   # a character must be of legal age
    assert e.value.code == 'CONTENT_BLOCKED'
    with pytest.raises(CoreFailure) as e:
        enchant.request('world', 'greeting', 'x', {})
    assert e.value.code == 'INVALID_INPUT'


def test_finish_cleans_and_guards_output(monkeypatch):
    import server.ego_core.content_guard as cg; monkeypatch.setattr(cg, 'NONCONSENT_FILTER', True)
    assert enchant.finish('world', 'description', 'Вот улучшенный текст:\n«Туманный порт.»') == 'Туманный порт.'
    assert enchant.finish('world', 'description', '```\nТуманный порт.\n```') == 'Туманный порт.'
    with pytest.raises(CoreFailure) as e:
        enchant.finish('world', 'description', '  ')
    assert e.value.code == 'ENCHANT_EMPTY'
    with pytest.raises(CoreFailure) as e:
        enchant.finish('character', 'scenario', 'Он раздевает её против её воли.')
    assert e.value.code == 'CONTENT_BLOCKED'
    with pytest.raises(CoreFailure) as e:
        enchant.finish('character', 'scenario', 'Сцена изнасилования.')
    assert e.value.code == 'CONTENT_BLOCKED'
    # card rules may speak of consent outside sex: not blocked
    assert enchant.finish('world', 'canon', '— Импланты нельзя ставить без согласия владельца.').startswith('— Импланты')
    # a heading line and a stray English tail are cleaned
    assert enchant.finish('character', 'personality', 'Личность и характер:\nТорн — не идеаломperfect.') == 'Торн — не идеалом.'


def test_enchant_api_returns_improved_field(core, monkeypatch, tmp_path):
    c, service, store, runtime, _ = core
    assert c.post('/v1/enchant', json={'kind': 'world', 'field': 'description', 'text': 'Порт'}).json()['code'] == 'RUNTIME_NOT_READY'
    log = _warm(c, runtime, monkeypatch, tmp_path)
    r = c.post('/v1/enchant', json={'kind': 'world', 'field': 'description', 'text': 'Порт в тумане',
                                    'context': {'name': 'Элдмур', 'canon': '— Магия запрещена'}})
    assert r.status_code == 200, r.text
    assert r.json() == {'kind': 'world', 'field': 'description', 'text': 'Туманный порт Элдмур живёт торговлей и слухами.'}
    sent = json.loads(log.read_text(encoding='utf-8').splitlines()[-1])
    assert sent[0]['content'].startswith('Ты помогаешь автору') and 'Магия запрещена' in sent[1]['content']
    status = service.status()
    assert status['runtime_state'] == 'READY' and status['enchant_job'] is False and status['active_generation'] is None
    assert c.post('/v1/enchant', json={'kind': 'world', 'field': 'description', 'text': 'Школьница раздевается'}).status_code == 422
    assert c.post('/v1/enchant', json={'kind': 'robot', 'field': 'description'}).status_code == 422
    assert c.post('/v1/enchant', json={'kind': 'world', 'field': 'description', 'extra': 1}).status_code == 422


def test_enchant_refuses_while_runtime_is_busy(core, monkeypatch, tmp_path):
    c, service, store, runtime, _ = core
    _warm(c, runtime, monkeypatch, tmp_path)
    with service._condition:
        service._active = ('memory:x', None, None)
    try:
        r = c.post('/v1/enchant', json={'kind': 'character', 'field': 'behavior', 'text': 'Говорит тихо'})
        assert r.json()['code'] == 'CORE_BUSY' and r.status_code == 409
    finally:
        with service._condition:
            service._active = None
