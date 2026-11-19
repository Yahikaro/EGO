"""Stage 4a: a preset per chat (sampling + reply length), stored by chat_id."""
from test_core_api import core, new_chat, uid, wait_terminal  # noqa: F401  (fixture reuse)


def test_chat_preset_is_stored_listed_and_validated(core):
    client, service, store, runtime, _ = core
    presets = client.get('/v1/presets').json()['presets']
    assert {'balanced-12gb', 'short-12gb'} <= {p['id'] for p in presets}
    assert presets[0]['sampling']['max_tokens'] > 0
    chat = new_chat(client)
    assert client.get(f"/v1/chats/{chat['id']}/settings").json() == {'chat_id': chat['id'], 'preset_id': None}
    assert client.put(f"/v1/chats/{chat['id']}/settings", json={'preset_id': 'short-12gb'}).status_code == 200
    assert client.get(f"/v1/chats/{chat['id']}/settings").json()['preset_id'] == 'short-12gb'
    assert client.put(f"/v1/chats/{chat['id']}/settings", json={'preset_id': 'no-such-preset'}).status_code == 404
    assert client.put(f"/v1/chats/{uid()}/settings", json={'preset_id': 'short-12gb'}).status_code == 404
    assert client.put(f"/v1/chats/{chat['id']}/settings", json={'preset_id': None}).json()['preset_id'] is None
    other = new_chat(client)
    assert client.get(f"/v1/chats/{other['id']}/settings").json()['preset_id'] is None   # per chat, not global


def test_generation_uses_the_chat_preset_and_default_chats_keep_the_profile(core, monkeypatch):
    client, service, store, runtime, _ = core
    calls = []
    original = runtime.stream_chat
    def capture(messages, *args, **kwargs):
        calls.append(kwargs)
        return original(messages, *args, **kwargs)
    monkeypatch.setattr(runtime, 'stream_chat', capture)
    tuned = new_chat(client)
    client.put(f"/v1/chats/{tuned['id']}/settings", json={'preset_id': 'short-12gb'})
    g = client.post('/v1/generations', json={'operation_id': uid(), 'chat_id': tuned['id'], 'content': 'привет',
                                             'expected_version': 1}).json()['generation']
    assert wait_terminal(client, g['id'])['state'] == 'completed'
    assert calls[-1]['sampling']['temperature'] == 0.7 and calls[-1]['max_tokens'] == 256
    plain = new_chat(client)
    g = client.post('/v1/generations', json={'operation_id': uid(), 'chat_id': plain['id'], 'content': 'привет',
                                             'expected_version': 1}).json()['generation']
    assert wait_terminal(client, g['id'])['state'] == 'completed'
    assert calls[-1] == {}                       # no preset: server profile values, exactly as before


def test_adapter_overrides_only_sampling_keys():
    from runtime.adapters.llama_cpp import LlamaRuntime
    import inspect
    source = inspect.getsource(LlamaRuntime.stream_chat)
    assert "for key in ('temperature','top_p','top_k','min_p','repeat_penalty')" in source
