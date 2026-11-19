"""Durable SQLCipher core contracts; disposable synthetic data only."""
import importlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from server.ego_core.vault.crypto_restore.storage import open_database

KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))


def store_module():
    assert importlib.util.find_spec('server.ego_core.store') is not None, 'encrypted core store is required'
    return importlib.import_module('server.ego_core.store')


def uid():
    return str(uuid4())


def assert_failure(code, action):
    with pytest.raises(store_module().StoreFailure) as exc:
        action()
    assert exc.value.code == code
    assert str(exc.value) == code


def new_store(tmp_path):
    return store_module().MainStore(tmp_path / 'main.db', KEY)


def test_real_encrypted_store_reopens_without_plaintext_in_database_or_wal(tmp_path):
    module = store_module()
    path = tmp_path / 'main.db'
    store = module.MainStore(path, KEY)
    marker = 'private synthetic payload 376943254'
    chat = store.create_chat(uid(), marker)
    generation = store.begin_generation(uid(), chat['id'], marker, chat['version'])
    store.set_generation_state(generation['id'], 'started')
    store.set_generation_state(generation['id'], 'completed', text=marker)
    # Keep a real SQLCipher reader open so the WAL remains observable.
    reader = open_database(path, KEY)
    reader.execute('BEGIN')
    reader.execute('SELECT count(*) FROM chats').fetchone()
    store.create_chat(uid(), marker)
    assert Path(str(path) + '-wal').exists()
    for file in tmp_path.iterdir():
        data = file.read_bytes()
        assert marker.encode() not in data
        assert not data.startswith(b'SQLite format 3')
    reader.rollback()
    reader.close()
    store.close()
    reopened = module.MainStore(path, KEY)
    assert reopened.generation(generation['id'])['state'] == 'completed'
    assert [m['content'] for m in reopened.messages(chat['id'])] == [marker, marker]
    assert_failure('DATABASE_LOCKED_OR_INVALID', lambda: module.MainStore(path, OTHER_KEY))
    reopened.close()


def test_migration_records_domain_version_and_refuses_profile_future_or_unknown_schema(tmp_path):
    module = store_module()
    path = tmp_path / 'main.db'
    module.MainStore(path, KEY).close()
    conn = open_database(path, KEY)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
    assert conn.execute('SELECT domain FROM store_metadata').fetchone() == ('main',)
    assert conn.execute('SELECT version FROM migration_records').fetchall() == [(1,)]
    conn.execute('PRAGMA user_version=2')
    conn.close()
    assert_failure('SCHEMA_TOO_NEW', lambda: module.MainStore(path, KEY))
    profile_path = tmp_path / 'profile.db'
    profile = module.ProfileStore(profile_path, OTHER_KEY)
    assert profile.status() == {'domain': 'profile', 'schema_version': 1}
    assert not hasattr(profile, 'messages')
    profile.close()
    assert_failure('DATABASE_LOCKED_OR_INVALID', lambda: module.MainStore(profile_path, KEY))
    assert_failure('DATABASE_DOMAIN_MISMATCH', lambda: module.MainStore(profile_path, OTHER_KEY))
    assert_failure('DATABASE_DOMAIN_MISMATCH', lambda: module.ProfileStore(path, KEY))
    unknown = tmp_path / 'unknown.db'
    conn = open_database(unknown, KEY)
    conn.execute('CREATE TABLE unrelated(secret TEXT)')
    conn.commit()
    conn.close()
    assert_failure('SCHEMA_INVALID', lambda: module.MainStore(unknown, KEY))


def test_idempotent_create_generation_conflicts_and_chat_isolation(tmp_path):
    store = new_store(tmp_path)
    op = uid()
    chat = store.create_chat(op, 'first')
    assert store.create_chat(op, 'first') == chat
    assert_failure('IDEMPOTENCY_CONFLICT', lambda: store.create_chat(op, 'changed'))
    other = store.create_chat(uid(), 'second')
    gen_op = uid()
    generation = store.begin_generation(gen_op, chat['id'], 'hello', chat['version'])
    assert store.begin_generation(gen_op, chat['id'], 'hello', chat['version']) == generation
    assert_failure('IDEMPOTENCY_CONFLICT', lambda: store.begin_generation(gen_op, chat['id'], 'changed', chat['version']))
    assert_failure('IDEMPOTENCY_CONFLICT', lambda: store.create_chat(gen_op, 'hello'))
    assert_failure('VERSION_CONFLICT', lambda: store.begin_generation(uid(), chat['id'], 'hello', chat['version']))
    version = next(c['version'] for c in store.list_chats() if c['id'] == chat['id'])
    assert version == chat['version'] + 1
    assert_failure('GENERATION_BUSY', lambda: store.begin_generation(uid(), chat['id'], 'hello', version))
    assert len(store.messages(chat['id'])) == 1
    assert store.messages(other['id']) == []
    journal = store.operation(gen_op)
    assert journal['kind'] == 'begin_generation'
    assert journal['status'] == 'queued'
    assert journal['result']['id'] == generation['id']
    events = store.events(0)
    assert [e['sequence'] for e in events] == sorted({e['sequence'] for e in events})
    assert events[-1]['generation_id'] == generation['id']
    assert store.events(events[-1]['sequence']) == []
    store.close()


def test_concurrent_retry_across_connections_writes_once(tmp_path):
    module = store_module()
    path = tmp_path / 'main.db'
    stores = [module.MainStore(path, KEY) for _ in range(4)]
    chat = stores[0].create_chat(uid(), 'race')
    op = uid()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda store: store.begin_generation(op, chat['id'], 'once', chat['version']), stores))
    assert len({g['id'] for g in results}) == 1
    assert len(stores[0].messages(chat['id'])) == 1
    assert len([e for e in stores[0].events(0) if e['generation_id']]) == 1
    for store in stores:
        store.close()


@pytest.mark.parametrize(('terminal', 'text', 'ai_count'), [('completed', '', 1), ('completed', 'done', 1), ('stopped', '', 0), ('stopped', 'partial', 1), ('failed', '', 0)])
def test_terminal_state_is_durable_immutable_and_commits_only_correct_messages(tmp_path, terminal, text, ai_count):
    store = new_store(tmp_path)
    chat = store.create_chat(uid(), terminal)
    generation = store.begin_generation(uid(), chat['id'], 'user', chat['version'])
    assert_failure('INVALID_STATE_TRANSITION', lambda: store.set_generation_state(generation['id'], 'streaming', text='bad'))
    store.set_generation_state(generation['id'], 'started')
    store.set_generation_state(generation['id'], 'streaming', text='partial')
    error = 'RUNTIME_FAILED' if terminal == 'failed' else None
    final = store.set_generation_state(generation['id'], terminal, text=text, error_code=error)
    assert final['state'] == terminal
    assert store.set_generation_state(generation['id'], terminal, text=text, error_code=error) == final
    assert_failure('TERMINAL_STATE_IMMUTABLE', lambda: store.set_generation_state(generation['id'], terminal, text='changed'))
    assert_failure('TERMINAL_STATE_IMMUTABLE', lambda: store.set_generation_state(generation['id'], 'started'))
    ai = [m for m in store.messages(chat['id']) if m['role'] == 'assistant']
    assert len(ai) == ai_count
    if ai:
        assert ai[0]['status'] == terminal
        assert ai[0]['content'] == text
    assert store.operation(generation['operation_id'])['status'] == terminal
    store.close()


def test_recovery_marks_interrupted_failed_once_without_resubmission(tmp_path):
    module = store_module()
    path = tmp_path / 'main.db'
    store = module.MainStore(path, KEY)
    generations = []
    for state in ['queued', 'started', 'streaming']:
        chat = store.create_chat(uid(), state)
        g = store.begin_generation(uid(), chat['id'], 'user', chat['version'])
        if state != 'queued':
            store.set_generation_state(g['id'], 'started')
        if state == 'streaming':
            store.set_generation_state(g['id'], state, text='interrupted')
        generations.append(g)
    store.close()
    store = module.MainStore(path, KEY)
    assert store.recover_interrupted() == 3
    assert store.recover_interrupted() == 0
    for g in generations:
        final = store.generation(g['id'])
        assert final['state'] == 'failed'
        assert final['error_code'] == 'SERVER_RESTARTED'
        assert len(store.messages(g['chat_id'])) == 1
    store.close()


def test_transaction_failure_rolls_back_message_journal_version_and_event(tmp_path):
    store = new_store(tmp_path)
    chat = store.create_chat(uid(), 'rollback')
    conn = open_database(tmp_path / 'main.db', KEY)
    conn.execute("CREATE TRIGGER reject_generation BEFORE INSERT ON generations BEGIN SELECT RAISE(ABORT, 'private diagnostic'); END")
    conn.commit()
    conn.close()
    op = uid()
    assert_failure('STORAGE_FAILURE', lambda: store.begin_generation(op, chat['id'], 'secret user', chat['version']))
    assert store.messages(chat['id']) == []
    assert store.list_chats()[0]['version'] == chat['version']
    assert_failure('OPERATION_NOT_FOUND', lambda: store.operation(op))
    assert len(store.events(0)) == 1
    store.close()


def test_partial_migration_is_transactionally_rolled_back(tmp_path, monkeypatch):
    module = store_module()
    real_open = module.open_database
    class Interrupted:
        def __init__(self, conn):
            self.conn = conn
        def __getattr__(self, attr):
            return getattr(self.conn, attr)
        def execute(self, sql, *args):
            if 'CREATE TABLE messages' in sql:
                raise RuntimeError('synthetic private failure')
            return self.conn.execute(sql, *args)
    monkeypatch.setattr(module, 'open_database', lambda path, key, **kwargs: Interrupted(real_open(path, key, **kwargs)))
    assert_failure('STORAGE_FAILURE', lambda: module.MainStore(tmp_path / 'main.db', KEY))
    conn = real_open(tmp_path / 'main.db', KEY)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    conn.close()


def test_validates_identifiers_bounds_errors_and_closed_store(tmp_path):
    store = new_store(tmp_path)
    assert_failure('INVALID_ID', lambda: store.create_chat('not-uuid-secret', 'title'))
    assert_failure('INVALID_ID', lambda: store.create_chat('00000000-0000-1000-8000-000000000000', 'title'))
    assert_failure('INVALID_INPUT', lambda: store.create_chat(uid(), 'x' * 201))
    assert_failure('INVALID_INPUT', lambda: store.create_chat(uid(), ''))
    chat = store.create_chat(uid(), 'valid')
    assert_failure('INVALID_INPUT', lambda: store.begin_generation(uid(), chat['id'], 'x' * 32769, chat['version']))
    assert_failure('INVALID_INPUT', lambda: store.begin_generation(uid(), chat['id'], 'x', True))
    assert_failure('NOT_FOUND', lambda: store.messages(uid()))
    generation = store.begin_generation(uid(), chat['id'], 'user', chat['version'])
    assert_failure('INVALID_INPUT', lambda: store.set_generation_state(generation['id'], 'failed', error_code='private path /text'))
    assert_failure('INVALID_INPUT', lambda: store.set_generation_state(generation['id'], 'failed', text='x' * 262145))
    assert_failure('INVALID_INPUT', lambda: store.events(-1))
    assert_failure('INVALID_INPUT', lambda: store.events(0, limit=101))
    store.close()
    assert_failure('STORE_CLOSED', store.list_chats)


def test_stop_command_is_atomic_idempotent_and_does_not_finalize_generation(tmp_path):
    store = new_store(tmp_path)
    chat = store.create_chat(uid(), 'stop')
    g = store.begin_generation(uid(), chat['id'], 'user', chat['version'])
    assert g['stop_requested'] is False
    op = uid()
    stopped = store.request_stop(op, g['id'])
    assert stopped['stop_requested'] is True
    assert stopped['state'] == 'queued'
    assert store.request_stop(op, g['id']) == stopped
    assert store.operation(op)['kind'] == 'stop_generation'
    assert store.operation(op)['status'] == 'completed'
    assert len([e for e in store.events(0) if e['type'] == 'generation.stop_requested']) == 1
    final = store.set_generation_state(g['id'], 'stopped', text='partial')
    assert store.request_stop(uid(), g['id']) == final
    assert store.generation(g['id']) == final
    assert_failure('IDEMPOTENCY_CONFLICT', lambda: store.request_stop(g['operation_id'], g['id']))
    assert_failure('NOT_FOUND', lambda: store.request_stop(uid(), uid()))
    store.close()


def test_shutdown_control_is_restricted_durable_and_idempotent(tmp_path):
    store = new_store(tmp_path)
    op = uid()
    receipt = store.record_control(op, 'shutdown')
    assert receipt == {'operation_id': op, 'kind': 'shutdown', 'status': 'completed'}
    assert store.record_control(op, 'shutdown') == receipt
    assert store.operation(op)['result'] == receipt
    assert_failure('INVALID_INPUT', lambda: store.record_control(uid(), 'destroy'))
    assert_failure('IDEMPOTENCY_CONFLICT', lambda: store.create_chat(op, 'changed kind'))
    store.close()
    store = new_store(tmp_path)
    assert store.operation(op)['result'] == receipt
    store.close()


def test_concurrent_initialization_creates_one_schema_and_bounded_latest_history(tmp_path):
    module = store_module()
    path = tmp_path / 'main.db'
    with ThreadPoolExecutor(max_workers=4) as pool:
        stores = list(pool.map(lambda _: module.MainStore(path, KEY), range(4)))
    store = stores[0]
    for i in range(102):
        store.create_chat(uid(), str(i))
    chats = store.list_chats()
    assert len(chats) == 100
    assert chats[0]['title'] == '101'
    assert chats[-1]['title'] == '2'
    chat = chats[0]
    version = chat['version']
    for i in range(51):
        g = store.begin_generation(uid(), chat['id'], str(i), version)
        store.set_generation_state(g['id'], 'started')
        store.set_generation_state(g['id'], 'completed', text='reply ' + str(i))
        version += 2
    messages = store.messages(chat['id'])
    assert len(messages) == 100
    assert messages[0]['content'] == '1'
    assert messages[-1]['content'] == 'reply 50'
    for item in stores:
        item.close()


@pytest.mark.parametrize('case', ['title', 'state', 'output'])
def test_invalid_unicode_and_untyped_state_fail_with_safe_codes(tmp_path, case):
    store = new_store(tmp_path)
    chat = store.create_chat(uid(), 'state')
    g = store.begin_generation(uid(), chat['id'], 'user', chat['version'])
    actions = {
        'title': lambda: store.create_chat(uid(), 'private\ud800text'),
        'state': lambda: store.set_generation_state(g['id'], {'private': 'text'}),
        'output': lambda: store.set_generation_state(g['id'], 'started', text='private\ud800text'),
    }
    assert_failure('INVALID_INPUT', actions[case])
    store.close()


def test_generation_events_reference_snapshots_without_copying_all_private_drafts(tmp_path):
    store=new_store(tmp_path)
    chat=store.create_chat(uid(),'bounded events')
    generation=store.begin_generation(uid(),chat['id'],'input',1)
    store.set_generation_state(generation['id'],'started')
    draft='synthetic private draft '*1000
    store.set_generation_state(generation['id'],'streaming',text=draft)
    events=store.events(0)
    assert all('text' not in event['payload'] for event in events if event['generation_id'])
    assert store.generation(generation['id'])['text']==draft
    store.close()
