"""Hard content limits on the model's reply (output side), enforced in code."""
from server.ego_core.content_guard import output_blocked
from test_persistent_service import persistent, wait_for, uid  # noqa: F401  (fixture reuse)


def test_output_guard_blocks_nonconsent_and_sexual_minor_context_only(monkeypatch):
    import server.ego_core.content_guard as cg; monkeypatch.setattr(cg, 'NONCONSENT_FILTER', True)
    assert output_blocked('Он продолжает насиловать меня, не обращая внимания на крики.')
    assert output_blocked('Она кричит, но он продолжает раздевать её.')
    assert not output_blocked('Она кричит, но он не останавливается и бьёт снова.')   # a fight, not sex
    assert output_blocked('Школьница раздевается, её грудь...')
    assert not output_blocked('Дети играли во дворе, пока Лира читала книгу.')      # no sexual context
    assert not output_blocked('Мы оба хотим этого. Я снимаю с тебя одежду, голая кожа горит.')
    assert not output_blocked('')


def test_blocked_reply_is_discarded_and_model_stays_ready(persistent, monkeypatch):
    import server.ego_core.content_guard as cg; monkeypatch.setattr(cg, 'NONCONSENT_FILTER', True)
    from runtime.adapters.llama_cpp import StreamEvent
    service, store, runtime = persistent
    service.start_runtime(); wait_for(lambda: service.status()['runtime_state'] == 'READY')
    original = runtime.stream_chat
    def reply(messages, *args, **kwargs):
        last = [m for m in messages if m['role'] == 'user'][-1]['content']
        if 'насил' not in last:
            yield from original(messages, *args, **kwargs); return
        for part in ('Он ', 'продолжает ', 'насиловать ', 'её.'):   # streamed like a real model
            yield StreamEvent(part)
        yield StreamEvent('', True, finish_reason='stop')
    monkeypatch.setattr(runtime, 'stream_chat', reply)
    chat = store.create_chat(uid(), 'guard')
    # The patched stream plays the model's forbidden reply; calling the service
    # directly skips the API input check, so only the output guard can stop it.
    generation = service.generate(uid(), chat['id'], 'Он продолжает насиловать её.', chat['version'])
    wait_for(lambda: store.generation(generation['id'])['state'] in {'completed', 'stopped', 'failed'})
    final = store.generation(generation['id'])
    assert final['state'] == 'failed' and final['error_code'] == 'CONTENT_BLOCKED' and final['text'] == ''
    assert [m['role'] for m in store.messages(chat['id'])] == ['user']       # no assistant message saved
    wait_for(lambda: service.status()['runtime_state'] == 'READY')
    assert service.status()['last_runtime_error'] is None
    chat = next(c for c in store.list_chats() if c['id'] == chat['id'])
    ok = service.generate(uid(), chat['id'], 'Привет, как дела?', chat['version'])
    wait_for(lambda: store.generation(ok['id'])['state'] in {'completed', 'stopped', 'failed'})
    assert store.generation(ok['id'])['state'] == 'completed'
