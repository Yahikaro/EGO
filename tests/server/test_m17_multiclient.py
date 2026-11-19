"""M1.7 multi-client integration: real uvicorn listener, real SQLCipher, fake llama child.

Covers what Phase B only mocked: several SSE clients on one generation, a dropped
stream resumed with Last-Event-ID, stop from another client, same-chat races and
cross-chat queueing, and history paging through the HTTP API.
"""
import json
import secrets
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn

from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeConfig
from runtime.adapters.ports import choose_loopback_port

TERMINAL = {'completed', 'stopped', 'failed'}


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def live(tmp_path, monkeypatch):
    from server.ego_core.api import create_app
    from server.ego_core.service import CoreService
    from server.ego_core.store import MainStore
    model = tmp_path / 'fixture.gguf'; model.write_bytes(b'GGUFtest')
    runtime = LlamaRuntime(RuntimeConfig(Path(sys.executable), model, choose_loopback_port(), startup_timeout=10))
    fixture = Path(__file__).parents[2] / 'tests/fixtures/fake_llama.py'
    monkeypatch.setattr(runtime, '_binary_command',
                        lambda: [getattr(sys, '_base_executable', sys.executable), str(fixture)])
    store = MainStore(tmp_path / 'main.db', secrets.token_bytes(32))
    service = CoreService(store, runtime=runtime)
    token = secrets.token_urlsafe(32)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(service, token), host='127.0.0.1', port=port,
                                           log_config=None, log_level='critical', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, 'uvicorn did not start'
        time.sleep(.02)
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'Authorization': 'Bearer ' + token},
                          timeout=30, trust_env=False)
    try:
        yield client, service
    finally:
        client.close()
        server.should_exit = True
        thread.join(10)
        service.close(); store.close()


def new_chat(client):
    response = client.post('/v1/chats', json={'operation_id': uid(), 'title': 'multi-client'})
    assert response.status_code == 200, response.text
    return response.json()


def generate(client, chat, content):
    response = client.post('/v1/generations', json={'operation_id': uid(), 'chat_id': chat['id'],
                                                   'content': content, 'expected_version': chat['version']})
    assert response.status_code == 202, response.text
    return response.json()['generation']


def read_sse(client, generation_id, *, last_event_id=None, stop_after=None):
    """Return [(event_id, snapshot)] until terminal, or after `stop_after` events."""
    headers = {'Last-Event-ID': last_event_id} if last_event_id else {}
    events, current_id, data = [], None, []
    with client.stream('GET', f'/v1/generations/{generation_id}/events', headers=headers) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith('id: '): current_id = line[4:]
            elif line.startswith('data: '): data.append(line[6:])
            elif line == '' and data:
                snapshot = json.loads('\n'.join(data)); data = []
                events.append((current_id, snapshot))
                if snapshot['state'] in TERMINAL or (stop_after and len(events) >= stop_after):
                    break
    return events


def wait_terminal(client, generation_id, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = client.get(f'/v1/generations/{generation_id}').json()
        if state['state'] in TERMINAL:
            return state
        time.sleep(.05)
    pytest.fail('generation did not finish')


def test_four_sse_clients_see_the_same_final_snapshot(live):
    client, _ = live
    generation = generate(client, new_chat(client), 'slow')  # 100 fragments over ~3 s
    results = [None] * 4

    def watch(index):
        with httpx.Client(base_url=client.base_url, headers=client.headers, timeout=30, trust_env=False) as own:
            results[index] = read_sse(own, generation['id'])

    threads = [threading.Thread(target=watch, args=(i,)) for i in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(30)
    finals = [events[-1] for events in results]
    assert all(snapshot['state'] == 'completed' and snapshot['text'] == 'x' * 100 for _, snapshot in finals)
    assert len({event_id for event_id, _ in finals}) == 1, 'identical snapshot -> identical cursor'
    assert all(len(events) >= 2 for events in results), 'clients saw intermediate drafts, not only the end'


def test_dropped_stream_resumes_with_cursor_without_duplicate(live):
    client, _ = live
    generation = generate(client, new_chat(client), 'slow')
    first = read_sse(client, generation['id'], stop_after=1)   # client "drops" after one event
    cursor, seen = first[0]
    resumed = read_sse(client, generation['id'], last_event_id=cursor)
    assert resumed[0][0] != cursor, 'the snapshot already seen is not sent again'
    assert resumed[-1][1]['state'] == 'completed' and resumed[-1][1]['text'] == 'x' * 100
    assert len(resumed[-1][1]['text']) >= len(seen['text'])


def test_stop_from_second_client_ends_first_clients_stream(live):
    client, service = live
    chat = new_chat(client)
    generation = generate(client, chat, 'slow')
    observed = {}
    watcher = threading.Thread(target=lambda: observed.setdefault('events', read_sse(client, generation['id'])))
    watcher.start()
    deadline = time.monotonic() + 15
    while not client.get(f"/v1/generations/{generation['id']}").json()['text']:
        assert time.monotonic() < deadline, 'no draft text arrived'
        time.sleep(.05)
    with httpx.Client(base_url=client.base_url, headers=client.headers, timeout=30, trust_env=False) as second:
        assert second.post(f"/v1/generations/{generation['id']}/stop", json={'operation_id': uid()}).status_code == 202
    watcher.join(15)
    final = observed['events'][-1][1]
    assert final['state'] == 'stopped' and 0 < len(final['text']) < 100
    assert wait_terminal(client, generation['id'])['state'] == 'stopped'
    # The model process survives a soft stop and serves the next request.
    chat = next(c for c in client.get('/v1/chats').json() if c['id'] == chat['id'])
    assert wait_terminal(client, generate(client, chat, 'next')['id'])['state'] == 'completed'
    assert service.status()['runtime_state'] == 'READY'


def test_same_chat_race_has_one_winner_and_other_chats_queue(live):
    client, _ = live
    chat = new_chat(client)
    codes = []

    def post(text):
        with httpx.Client(base_url=client.base_url, headers=client.headers, timeout=30, trust_env=False) as own:
            response = own.post('/v1/generations', json={'operation_id': uid(), 'chat_id': chat['id'],
                                                         'content': text, 'expected_version': chat['version']})
            codes.append((response.status_code, response.json().get('code')))

    racers = [threading.Thread(target=post, args=(f'race {i}',)) for i in range(3)]
    for thread in racers: thread.start()
    for thread in racers: thread.join(15)
    assert sorted(status for status, _ in codes) == [202, 409, 409]
    assert {code for status, code in codes if status == 409} <= {'VERSION_CONFLICT', 'GENERATION_BUSY'}
    # Different chats are accepted together and complete one after another.
    others = [generate(client, new_chat(client), 'slow') for _ in range(2)]
    assert all(wait_terminal(client, g['id'], 30)['state'] == 'completed' for g in others)


def test_history_paging_is_additive(live):
    client, _ = live
    chat = new_chat(client)
    for i in range(3):
        generation = generate(client, chat, f'm{i}')
        wait_terminal(client, generation['id'])
        chat = next(c for c in client.get('/v1/chats').json() if c['id'] == chat['id'])
    everything = client.get(f"/v1/chats/{chat['id']}/messages").json()
    assert [m['role'] for m in everything] == ['user', 'assistant'] * 3
    latest = client.get(f"/v1/chats/{chat['id']}/messages", params={'limit': 2}).json()
    assert latest == everything[-2:]
    older = client.get(f"/v1/chats/{chat['id']}/messages", params={'limit': 2, 'before': latest[0]['id']}).json()
    assert older == everything[-4:-2]
    assert client.get(f"/v1/chats/{chat['id']}/messages", params={'limit': 0}).status_code == 422
    assert client.get(f"/v1/chats/{chat['id']}/messages", params={'before': uid()}).status_code == 404
