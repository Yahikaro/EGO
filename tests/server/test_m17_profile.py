"""2026-11-06: owner profile — fields, avatar, and what reaches the model (name and address only)."""
import json

from test_core_api import LocalClient, core, new_chat, uid, wait_terminal  # noqa: F401  (fixture reuse)

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64


def test_profile_fields_and_avatar(core):
    c, *_ = core
    got = c.get('/v1/profile').json()
    assert got['profile']['display_name'] == '' and got['profile']['use_in_stories'] is True and got['has_avatar'] is False
    body = {'display_name': 'Мира', 'nickname': 'Мирка', 'pronouns': 'she', 'pronouns_text': '', 'description': 'Люблю мистику',
            'accent_colour': 'ember', 'use_in_stories': True}
    assert c.put('/v1/profile', json={'profile': body}).json()['profile'] == body
    assert c.put('/v1/profile', json={'profile': {'pronouns': 'x'}}).status_code == 422
    assert c.put('/v1/profile', json={'profile': {'unknown': 1}}).status_code == 422
    assert c.request('GET', '/v1/profile/avatar').status_code == 404
    r = c.request('PUT', '/v1/profile/avatar', content=JPEG, headers={'Content-Type': 'image/jpeg'})
    assert r.status_code == 200 and r.json()['has_avatar'] is True
    assert c.request('GET', '/v1/profile/avatar').content == JPEG
    assert c.request('PUT', '/v1/profile/avatar', content=b'not an image', headers={'Content-Type': 'image/jpeg'}).status_code == 422
    assert c.put('/v1/profile', json={'profile': body}).json()['has_avatar'] is True     # fields keep the avatar
    assert c.request('DELETE', '/v1/profile/avatar').json()['has_avatar'] is False


def _system_prompt(c, runtime, monkeypatch, tmp_path):
    log = tmp_path / 'requests.jsonl'
    original = runtime.child_environment
    monkeypatch.setattr(runtime, 'child_environment', lambda: {**original(), 'EGO_FAKE_CHAT_LOG': str(log)})
    chat = new_chat(c, 'профиль')
    r = c.post('/v1/generations', json={'operation_id': uid(), 'chat_id': chat['id'], 'content': 'Привет', 'expected_version': chat['version'],
                                        'character_name': 'Торн', 'character_description': 'Странник.'})
    assert wait_terminal(c, r.json()['generation']['id'])['state'] == 'completed'
    return json.loads(log.read_text(encoding='utf-8').splitlines()[-1])[0]['content']


def test_only_name_and_address_reach_the_model(core, monkeypatch, tmp_path):
    c, service, store, runtime, _ = core
    c.put('/v1/profile', json={'profile': {'display_name': 'Мира', 'nickname': 'Мирка', 'pronouns': 'she',
                                           'description': 'СЕКРЕТНОЕ о себе', 'use_in_stories': True}})
    system = _system_prompt(c, runtime, monkeypatch, tmp_path)
    assert 'Собеседника зовут Мира' in system and 'женском роде' in system and '«Мирка»' in system
    assert 'СЕКРЕТНОЕ' not in system


def test_switch_off_keeps_profile_out_of_the_prompt(core, monkeypatch, tmp_path):
    c, service, store, runtime, _ = core
    c.put('/v1/profile', json={'profile': {'display_name': 'Мира', 'pronouns': 'she', 'use_in_stories': False}})
    assert 'Мира' not in _system_prompt(c, runtime, monkeypatch, tmp_path)
