"""2026-11-06: chat library with client metadata (shared story list) and full chat deletion."""
import time

from test_core_api import LocalClient, core, new_chat, uid, wait_terminal  # noqa: F401  (fixture reuse)


def _say(c, chat_id, text):
    version = [x for x in c.get('/v1/chats').json() if x['id'] == chat_id][0]['version']
    r = c.post('/v1/generations', json={'operation_id': uid(), 'chat_id': chat_id, 'content': text, 'expected_version': version})
    assert r.status_code == 202, r.text
    return r.json()['generation']


def test_library_lists_meta_and_counts(core):
    c, *_ = core
    chat = new_chat(c, 'Тихая библиотека')
    meta = {'person': 'lira', 'members': ['lira'], 'opening': [{'by': 'ai', 'blocks': [['action', 'Туман.']]}]}
    r = c.put(f"/v1/chats/{chat['id']}/meta", json={'meta': meta, 'title': 'Библиотека'})
    assert r.status_code == 200
    assert wait_terminal(c, _say(c, chat['id'], 'Привет')['id'])['state'] == 'completed'
    row = [x for x in c.get('/v1/chats/library').json()['chats'] if x['id'] == chat['id']][0]
    assert row['meta'] == meta and row['title'] == 'Библиотека' and row['messages'] == 2 and row['last_message_at']
    assert c.put(f"/v1/chats/{uid()}/meta", json={'meta': {}}).status_code == 404
    assert c.put("/v1/chats/bad/meta", json={'meta': {}}).json()['code'] == 'INVALID_ID'


def test_delete_removes_chat_messages_memory_and_settings(core):
    c, service, store, *_ = core
    chat = new_chat(c, 'удалить')
    assert wait_terminal(c, _say(c, chat['id'], 'Раз')['id'])['state'] == 'completed'
    c.post(f"/v1/chats/{chat['id']}/memory", json={'text': 'Тайный факт'})
    c.put(f"/v1/chats/{chat['id']}/settings", json={'preset_id': 'short-12gb'})
    c.put(f"/v1/chats/{chat['id']}/meta", json={'meta': {'person': 'thorn'}})
    r = c.request('DELETE', f"/v1/chats/{chat['id']}")
    assert r.status_code == 200 and r.json() == {'chat_id': chat['id'], 'deleted': True}
    assert c.get(f"/v1/chats/{chat['id']}/messages").status_code == 404
    assert c.get(f"/v1/chats/{chat['id']}/memory").status_code == 404
    assert chat['id'] not in [x['id'] for x in c.get('/v1/chats/library').json()['chats']]
    with store._transaction(write=False) as conn:
        for table in ('messages', 'generations', 'chat_memory', 'chat_settings', 'chat_meta', 'events'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE chat_id=?', (chat['id'],)).fetchone()[0] == 0, table
    assert c.request('DELETE', f"/v1/chats/{chat['id']}").status_code == 404
    # other chats keep working
    other = new_chat(c, 'остаётся')
    assert wait_terminal(c, _say(c, other['id'], 'Два')['id'])['state'] == 'completed'


def test_delete_refused_while_a_reply_is_running(core):
    c, *_ = core
    chat = new_chat(c, 'занято')
    generation = _say(c, chat['id'], 'slow')
    deadline = time.monotonic() + 5
    while c.get('/v1/generations/' + generation['id']).json()['state'] == 'queued' and time.monotonic() < deadline:
        time.sleep(.02)
    r = c.request('DELETE', f"/v1/chats/{chat['id']}")
    assert r.status_code == 409 and r.json()['code'] == 'GENERATION_BUSY'
    wait_terminal(c, generation['id'])
    assert c.request('DELETE', f"/v1/chats/{chat['id']}").status_code == 200
