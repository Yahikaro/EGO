"""Stage 4c: each reply records why it ended; a reply cut by the token limit can be continued."""
from runtime.adapters.llama_cpp import StreamEvent
from test_core_api import core, new_chat, uid, wait_terminal  # noqa: F401  (fixture reuse)


def test_finish_reason_is_stored_per_generation_and_continue_extends_the_cut_reply(core, monkeypatch):
    client, service, store, runtime, _ = core
    original = runtime.stream_chat
    plan = {'reason': 'length'}

    def reply(messages, *args, **kwargs):
        if plan['reason'] is None:
            yield from original(messages, *args, **kwargs); return
        parts = ('Начало ', 'длинной ', 'истории') if plan['reason'] == 'length' else (' и её ', 'окончание.')
        for part in parts:
            yield StreamEvent(part)
        yield StreamEvent('', True, finish_reason=plan['reason'], tokens_predicted=3)
    monkeypatch.setattr(runtime, 'stream_chat', reply)

    chat = new_chat(client)
    first = client.post('/v1/generations', json={'operation_id': uid(), 'chat_id': chat['id'],
                                                 'content': 'Расскажи длинную историю', 'expected_version': 1}).json()['generation']
    cut = wait_terminal(client, first['id'])
    assert cut['state'] == 'completed' and cut['finish_reason'] == 'length'
    plan['reason'] = 'stop'
    version = next(c for c in client.get('/v1/chats').json() if c['id'] == chat['id'])['version']
    more = client.post('/v1/generations/continue', json={'operation_id': uid(), 'chat_id': chat['id'],
                                                         'expected_version': version}).json()['generation']
    done = wait_terminal(client, more['id'])
    assert done['state'] == 'completed' and done['finish_reason'] == 'stop'
    messages = client.get(f"/v1/chats/{chat['id']}/messages").json()
    assert [m['role'] for m in messages] == ['user', 'assistant']          # continued in place, no new message
    assert messages[-1]['content'].startswith('Начало длинной истории') and len(messages[-1]['content']) > len(cut['text'])


def test_unfinished_or_failed_generations_have_no_finish_reason(core):
    client, service, store, runtime, _ = core
    chat = new_chat(client)
    g = client.post('/v1/generations', json={'operation_id': uid(), 'chat_id': chat['id'], 'content': 'малформед',
                                             'expected_version': 1}).json()['generation']
    assert 'finish_reason' in client.get(f"/v1/generations/{g['id']}").json()
