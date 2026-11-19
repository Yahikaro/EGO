"""Decision D2: characters and worlds stored in the encrypted Core store."""
import json
import secrets
import uuid

import pytest

from server.ego_core.characters import compose_profile, find_world
from server.ego_core.store import MainStore, StoreFailure
from test_core_api import LocalClient, core, new_chat, uid, wait_terminal  # noqa: F401  (fixture reuse)

KEY = secrets.token_bytes(32)
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64
LIRA = {'name': 'Лира', 'age': '27', 'role': 'Хранительница архива', 'world': 'Эфир', 'setting': 'Библиотека',
         'personality': 'Ироничная, тёплая', 'greeting': '— Знаешь, некоторые истории не любят, когда их находят.',
         'tags': ['Мистика'], 'avatar': 'data:image/jpeg;base64,AAAA'}
ETHER = {'name': 'Эфир', 'genre': 'Мистика', 'description': 'Мир, где книги помнят будущее.', 'locations': []}


@pytest.fixture
def store(tmp_path):
    value = MainStore(tmp_path / 'main.db', KEY)
    yield value
    value.close()


def test_records_are_versioned_idempotent_and_encrypted_on_disk(store, tmp_path):
    assert store.put_record('character', 'lira', LIRA, expected_version=0) == {'id': 'lira', 'version': 1}
    assert store.put_record('character', 'lira', LIRA) == {'id': 'lira', 'version': 1}  # unchanged replay
    with pytest.raises(StoreFailure, match='VERSION_CONFLICT'):
        store.put_record('character', 'lira', {**LIRA, 'role': 'x'}, expected_version=0)
    assert store.put_record('character', 'lira', {**LIRA, 'role': 'Архивариус'}, expected_version=1)['version'] == 2
    record = store.get_record('character', 'lira')
    assert record['data']['role'] == 'Архивариус' and 'avatar' not in record['data']
    store.put_record('world', 'w-ether', ETHER)
    assert [w['id'] for w in store.list_records('world')] == ['w-ether']
    store.close()
    raw = (tmp_path / 'main.db').read_bytes() + b''.join(p.read_bytes() for p in tmp_path.glob('main.db-*'))
    assert 'Архивариус'.encode() not in raw and b'Ether' not in raw
    reopened = MainStore(tmp_path / 'main.db', KEY)
    assert reopened.get_record('character', 'lira')['version'] == 2
    reopened.close()


def test_avatar_is_separate_validated_and_deleted_with_character(store):
    store.put_record('character', 'lira', LIRA)
    with pytest.raises(StoreFailure, match='INVALID_INPUT'):
        store.put_avatar('lira', 'image/png', JPEG)            # signature must match type
    with pytest.raises(StoreFailure, match='NOT_FOUND'):
        store.put_avatar('nobody', 'image/jpeg', JPEG)
    store.put_avatar('lira', 'image/jpeg', JPEG)
    assert store.get_avatar('lira') == ('image/jpeg', JPEG)
    assert store.list_records('character')[0]['has_avatar'] is True
    first = store.list_records('character')[0]['avatar']
    assert first and ':' in first                                   # fingerprint of the stored picture
    store.put_avatar('lira', 'image/jpeg', JPEG + b'')
    assert store.list_records('character')[0]['avatar'] != first    # a changed picture is noticed
    store.delete_avatar('lira')
    assert store.list_records('character')[0]['avatar'] == ''       # a removed one too
    store.put_avatar('lira', 'image/jpeg', JPEG)
    store.delete_record('character', 'lira')
    with pytest.raises(StoreFailure, match='NOT_FOUND'):
        store.get_avatar('lira')


def test_record_input_is_bounded(store):
    for bad in ('', 'a/b', 'x' * 65, '../etc'):
        with pytest.raises(StoreFailure, match='INVALID_ID'):
            store.put_record('character', bad, LIRA)
    for data in ({}, {'name': ''}, {'name': 'x' * 121}, ['not', 'object'], {'name': 'A', 'big': 'я' * 120_000}):
        with pytest.raises(StoreFailure, match='INVALID_INPUT'):
            store.put_record('character', 'a', data)
    for i in range(100):
        store.put_record('world', f'w{i}', {'name': f'W{i}'})
    with pytest.raises(StoreFailure, match='RECORD_LIMIT'):
        store.put_record('world', 'one-more', {'name': 'Extra'})


def test_profile_composition_includes_world_and_stays_bounded():
    world = find_world(LIRA, [{'id': 'w-ether', 'data': ETHER}])
    name, description = compose_profile(LIRA, world)
    assert name == 'Лира'
    for part in ('Возраст: 27', 'Характер: Ироничная', 'Мир и место: Эфир · Библиотека',
                 'Описание мира: Мир, где книги помнят', 'Первая реплика Лира'):
        assert part in description
    assert 'data:image' not in description
    _, long = compose_profile({**LIRA, 'personality': 'я' * 5000, 'behavior': 'б' * 5000, 'scenario': 'с' * 5000})
    assert len(long) <= 12000


def test_api_crud_avatar_limits_and_client_token(core):
    client, service, store, runtime, token = core
    assert client.put('/v1/characters/lira', json={'data': LIRA, 'expected_version': 0}).json()['version'] == 1
    assert client.put('/v1/characters/lira', json={'data': LIRA, 'expected_version': 0}).status_code == 409
    big = {**LIRA, 'personality': 'я' * 30_000}                 # > 64 KiB JSON body, allowed for records
    assert client.put('/v1/characters/lira', json={'data': big}).status_code == 200
    assert client.post('/v1/chats', json={'operation_id': uid(), 'title': 'x' * 70_000}).status_code == 413
    photo = b'\xff\xd8\xff\xe0' + b'\x01' * (1_500_000)
    assert client.put('/v1/characters/lira/avatar', content=photo, headers={'content-type': 'image/jpeg'}).status_code == 200
    got = client.get('/v1/characters/lira/avatar')
    assert got.status_code == 200 and got.headers['content-type'] == 'image/jpeg' and got.content == photo
    huge = b'\xff\xd8\xff' + b'\x00' * (2 * 1024 * 1024 + 2048)
    assert client.put('/v1/characters/lira/avatar', content=huge, headers={'content-type': 'image/jpeg'}).status_code == 413
    assert client.put('/v1/characters/lira/avatar', content=b'GIF89a', headers={'content-type': 'image/gif'}).status_code == 422
    assert client.put('/v1/worlds/w-ether', json={'data': ETHER}).status_code == 200
    listed = client.get('/v1/characters').json()['characters']
    assert listed[0]['has_avatar'] and 'avatar' not in listed[0]['data']
    # A client session (not the admin token) can manage its own characters.
    from server.ego_core.api import create_app
    app = create_app(service, token)
    admin = LocalClient(app); admin.headers['authorization'] = 'Bearer ' + token
    code = admin.post('/v1/client/launch').json()['code']
    browser = LocalClient(app)
    browser.headers['authorization'] = 'Bearer ' + browser.post('/v1/client/session', json={'code': code}).json()['token']
    assert browser.get('/v1/worlds').json()['worlds'][0]['id'] == 'w-ether'
    assert browser.request('DELETE', '/v1/characters/lira/avatar').status_code == 200
    assert browser.request('DELETE', '/v1/characters/lira', params={'expected_version': 99}).status_code == 409
    assert browser.request('DELETE', '/v1/characters/lira').status_code == 200
    assert browser.get('/v1/characters').json() == {'characters': []}


def test_generation_by_character_id_uses_stored_profile_and_world(core, monkeypatch):
    client, service, store, runtime, _ = core
    client.put('/v1/characters/lira', json={'data': LIRA})
    client.put('/v1/worlds/w-ether', json={'data': ETHER})
    prompts = []
    original = runtime.stream_chat
    monkeypatch.setattr(runtime, 'stream_chat', lambda messages, *a, **k: (prompts.append(messages), original(messages, *a, **k))[1])
    chat = new_chat(client)
    body = {'operation_id': uid(), 'chat_id': chat['id'], 'content': 'Привет', 'expected_version': 1, 'character_id': 'lira'}
    generation = client.post('/v1/generations', json=body).json()['generation']
    assert wait_terminal(client, generation['id'])['state'] == 'completed'
    system = prompts[0][0]['content']
    assert 'Ты играешь персонажа Лира' in system and 'Описание мира: Мир, где книги помнят будущее.' in system
    assert 'data:image' not in json.dumps(prompts[0], ensure_ascii=False)
    both = {**body, 'operation_id': uid(), 'expected_version': 3, 'character_name': 'Другая'}
    assert client.post('/v1/generations', json=both).status_code == 422
    unknown = {**body, 'operation_id': uid(), 'expected_version': 3, 'character_id': 'nobody'}
    assert client.post('/v1/generations', json=unknown).status_code == 404
