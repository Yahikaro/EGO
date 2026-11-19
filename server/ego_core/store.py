"""Encrypted authoritative core storage; profile data has a separate domain.

Every connection is a verified SQLCipher connection. One lock serializes a
connection across worker threads; BEGIN IMMEDIATE also serializes other store
instances/processes. Transactions include journals, state, messages and events.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from uuid import UUID, uuid4

from server.ego_core.vault.crypto_restore.storage import StorageFailure, open_database


class StoreFailure(RuntimeError):
    """Stable public error code without underlying SQL or user text."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _ThreadSafeDriver:
    """Use native cross-thread support only behind the store connection lock."""
    def __init__(self):
        try:
            from sqlcipher3 import dbapi2
        except ImportError:
            raise StoreFailure('SQLCIPHER_UNAVAILABLE') from None
        self.backend = dbapi2
        self.DatabaseError = dbapi2.DatabaseError

    def connect(self, path):
        return self.backend.connect(path, check_same_thread=False, timeout=10)


# Bounds for client-defined records (decision D2). The client caps text fields
# (~24k characters per character card); UTF-8 Cyrillic doubles the byte size.
MAX_RECORD_BYTES = 200_000
MAX_RECORDS = {'character': 300, 'world': 100}
MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_SIGNATURES = {'image/jpeg': b'\xff\xd8\xff', 'image/png': b'\x89PNG\r\n\x1a\n', 'image/webp': b'RIFF'}


def _uuid(value):
    try:
        parsed = UUID(value) if isinstance(value, str) else None
        if parsed is None or parsed.version != 4 or str(parsed) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise StoreFailure('INVALID_ID') from None


def _bounded(value, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise StoreFailure('INVALID_INPUT')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise StoreFailure('INVALID_INPUT') from None


def _digest(kind, payload):
    encoded = json.dumps([kind, payload], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _row(cursor):
    item = cursor.fetchone()
    return None if item is None else dict(zip([c[0] for c in cursor.description], item))


def _rows(cursor):
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, item)) for item in cursor.fetchall()]


_BASE_SCHEMA = [
    'CREATE TABLE store_metadata(domain TEXT NOT NULL UNIQUE)',
    'CREATE TABLE migration_records(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)',
]
_MAIN_SCHEMA = [
    '''CREATE TABLE chats(id TEXT PRIMARY KEY, title TEXT NOT NULL, version INTEGER NOT NULL,
       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
    '''CREATE TABLE messages(sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
       chat_id TEXT NOT NULL REFERENCES chats(id), operation_id TEXT NOT NULL,
       role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN ('completed','stopped')), version INTEGER NOT NULL DEFAULT 1,
       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
    'CREATE INDEX messages_chat ON messages(chat_id, sequence)',
    '''CREATE TABLE operation_journal(operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
       digest TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL,
       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
    '''CREATE TABLE generations(id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE
       REFERENCES operation_journal(operation_id), chat_id TEXT NOT NULL REFERENCES chats(id),
       state TEXT NOT NULL CHECK(state IN ('queued','started','streaming','completed','stopped','failed')),
       text TEXT NOT NULL DEFAULT '', error_code TEXT,
       stop_requested INTEGER NOT NULL DEFAULT 0 CHECK(stop_requested IN (0,1)),
       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
    "CREATE UNIQUE INDEX generations_active_chat ON generations(chat_id) WHERE state IN ('queued','started','streaming')",
    '''CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
       chat_id TEXT REFERENCES chats(id), generation_id TEXT REFERENCES generations(id), payload TEXT NOT NULL,
       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
]
_PROFILE_SCHEMA = [
    '''CREATE TABLE profile_facts(id TEXT PRIMARY KEY, content TEXT NOT NULL,
       version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
]


_OPEN_LOCKS = {}
_OPEN_LOCKS_GUARD = threading.Lock()


def _open_lock(path):
    key = os.path.normcase(os.path.abspath(str(path)))
    with _OPEN_LOCKS_GUARD:
        return _OPEN_LOCKS.setdefault(key, threading.Lock())


class _EncryptedStore:
    def __init__(self, path: Path, key: bytes, domain: str):
        self._lock = threading.RLock()
        self._closed = False
        self._connection = None
        try:
            # Serialize first open + migration of one file inside this process:
            # a second connection reading a half-written encrypted header gets
            # "not a database" (not SQLITE_BUSY), which no busy timeout retries.
            # Other processes are excluded by the Core data lease.
            with _open_lock(path):
                # A concurrent creator can win the helper's exclusive file creation.
                try:
                    self._connection = open_database(Path(path), key, driver=_ThreadSafeDriver())
                except FileExistsError:
                    self._connection = open_database(Path(path), key, driver=_ThreadSafeDriver())
                self._connection.execute('PRAGMA busy_timeout = 10000')
                self._connection.execute('PRAGMA synchronous = FULL')
                self._connection.execute('PRAGMA secure_delete = ON')
                self._migrate(domain)
        except Exception as error:
            if self._connection is not None:
                self._connection.close()
            self._closed = True
            self._raise_safe(error)

    @staticmethod
    def _raise_safe(error):
        if isinstance(error, StoreFailure):
            raise error
        if isinstance(error, StorageFailure):
            code = str(error)
            if code in {'INVALID_KEY', 'SQLCIPHER_UNAVAILABLE', 'DATABASE_LOCKED_OR_INVALID'}:
                raise StoreFailure(code) from None
        raise StoreFailure('STORAGE_FAILURE') from None

    @contextmanager
    def _transaction(self, *, write=True):
        with self._lock:
            if self._closed:
                raise StoreFailure('STORE_CLOSED')
            try:
                self._connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield self._connection
                self._connection.commit()
            except Exception as error:
                self._connection.rollback()
                self._raise_safe(error)

    def _migrate(self, domain):
        with self._transaction() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            # Check the domain before the version so another encrypted domain
            # is never treated as a candidate schema for this store.
            if 'store_metadata' in tables:
                rows = conn.execute('SELECT domain FROM store_metadata').fetchall()
                if rows != [(domain,)]:
                    raise StoreFailure('DATABASE_DOMAIN_MISMATCH')
            elif tables:
                raise StoreFailure('SCHEMA_INVALID')
            if version > 1:
                raise StoreFailure('SCHEMA_TOO_NEW')
            if version == 0:
                if tables:
                    raise StoreFailure('SCHEMA_INVALID')
                for statement in _BASE_SCHEMA + (_MAIN_SCHEMA if domain == 'main' else _PROFILE_SCHEMA):
                    conn.execute(statement)
                conn.execute('INSERT INTO store_metadata(domain) VALUES (?)', (domain,))
                conn.execute('INSERT INTO migration_records(version) VALUES (1)')
                conn.execute('PRAGMA user_version = 1')
            elif version == 1:
                expected = {'store_metadata', 'migration_records'} | (
                    {'chats', 'messages', 'operation_journal', 'generations', 'events'}
                    if domain == 'main' else {'profile_facts'})
                if not expected.issubset(tables):
                    raise StoreFailure('SCHEMA_INVALID')
                if conn.execute('SELECT version FROM migration_records ORDER BY version').fetchall() != [(1,)]:
                    raise StoreFailure('SCHEMA_INVALID')
            else:
                raise StoreFailure('SCHEMA_INVALID')
            # Additive M1.5 table; existing SQLCipher chat DB and version 1 data remain intact.
            if domain == 'main':
                conn.execute('''CREATE TABLE IF NOT EXISTS regeneration_targets(
                    generation_id TEXT PRIMARY KEY REFERENCES generations(id),
                    message_id TEXT NOT NULL REFERENCES messages(id))''')
                conn.execute('''CREATE TABLE IF NOT EXISTS continuation_targets(
                    generation_id TEXT PRIMARY KEY REFERENCES generations(id),
                    message_id TEXT NOT NULL REFERENCES messages(id))''')
                # Additive M1.7 tables (decision D2): characters and worlds live in
                # the encrypted Core store; the client keeps only a cache.
                conn.execute('''CREATE TABLE IF NOT EXISTS characters(
                    id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
                conn.execute('''CREATE TABLE IF NOT EXISTS character_avatars(
                    character_id TEXT PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
                    media_type TEXT NOT NULL, data BLOB NOT NULL)''')
                # 2026-11-08: pictures generated through ComfyUI (media.py); params JSON = prompt, seed, LoRA …
                conn.execute('''CREATE TABLE IF NOT EXISTS media_images(
                    id TEXT PRIMARY KEY, media_type TEXT NOT NULL, data BLOB NOT NULL, params TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
                # 2026-11-08: user voice samples for local TTS (tts.py); meta JSON = name, languages, ref_text …
                conn.execute('''CREATE TABLE IF NOT EXISTS tts_voices(
                    id TEXT PRIMARY KEY, meta TEXT NOT NULL, data BLOB NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
                conn.execute('''CREATE TABLE IF NOT EXISTS worlds(
                    id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
                # Additive M1.7 stage 4a: per-chat generation preset.
                conn.execute('''CREATE TABLE IF NOT EXISTS chat_settings(
                    chat_id TEXT PRIMARY KEY REFERENCES chats(id), preset_id TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
                # Additive 2026-11-06: client metadata of a chat (character, members, opening scene)
                # so another device can rebuild the story.
                conn.execute('''CREATE TABLE IF NOT EXISTS chat_meta(
                    chat_id TEXT PRIMARY KEY REFERENCES chats(id), data TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
                # Additive M1.7 stage 4c: why each reply ended (stop | length).
                conn.execute('''CREATE TABLE IF NOT EXISTS generation_meta(
                    generation_id TEXT PRIMARY KEY REFERENCES generations(id),
                    finish_reason TEXT, completion_tokens INTEGER)''')

    def close(self):
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True


class ProfileStore(_EncryptedStore):
    """Separate encrypted profile domain; no generation context reader."""
    def __init__(self, path: Path, key: bytes):
        super().__init__(path, key, 'profile')

    def status(self):
        with self._transaction(write=False):
            return {'domain': 'profile', 'schema_version': 1}


class MainStore(_EncryptedStore):
    def __init__(self, path: Path, key: bytes):
        super().__init__(path, key, 'main')

    @staticmethod
    def _generation(conn, generation_id):
        result = _row(conn.execute('SELECT * FROM generations WHERE id=?', (generation_id,)))
        if result is None:
            raise StoreFailure('NOT_FOUND')
        result['stop_requested'] = bool(result['stop_requested'])
        try:
            meta = conn.execute('SELECT finish_reason FROM generation_meta WHERE generation_id=?',
                                (generation_id,)).fetchone()
        except Exception:
            meta = None  # Store opened without the additive table (older schema in tests).
        result['finish_reason'] = meta[0] if meta else None
        return result

    @staticmethod
    def _operation(conn, operation_id):
        result = _row(conn.execute('SELECT operation_id,kind,status,result FROM operation_journal WHERE operation_id=?', (operation_id,)))
        if result is None:
            raise StoreFailure('OPERATION_NOT_FOUND')
        result['result'] = json.loads(result['result'])
        return result

    @staticmethod
    def _replay(conn, operation_id, digest):
        row = _row(conn.execute('SELECT digest,result FROM operation_journal WHERE operation_id=?', (operation_id,)))
        if row is None:
            return None
        if row['digest'] != digest:
            raise StoreFailure('IDEMPOTENCY_CONFLICT')
        return json.loads(row['result'])

    @staticmethod
    def _event(conn, event_type, chat_id, generation_id, payload):
        if generation_id is not None:
            # Durable cursors point to the current encrypted snapshot. Copying
            # every cumulative draft into the journal grows quadratically.
            payload = {k: payload[k] for k in ('id', 'state', 'stop_requested') if k in payload}
        conn.execute('INSERT INTO events(type,chat_id,generation_id,payload) VALUES(?,?,?,?)',
                     (event_type, chat_id, generation_id, json.dumps(payload, ensure_ascii=False)))

    def create_chat(self, operation_id: str, title: str):
        _uuid(operation_id)
        _bounded(title, 200)
        digest = _digest('create_chat', {'title': title})
        with self._transaction() as conn:
            replay = self._replay(conn, operation_id, digest)
            if replay is not None:
                return replay
            result = {'id': str(uuid4()), 'title': title, 'version': 1}
            conn.execute('INSERT INTO chats(id,title,version) VALUES(?,?,?)', (result['id'], title, 1))
            conn.execute('INSERT INTO operation_journal(operation_id,kind,digest,status,result) VALUES(?,?,?,?,?)',
                         (operation_id, 'create_chat', digest, 'completed', json.dumps(result)))
            self._event(conn, 'chat.created', result['id'], None, result)
            return result

    def list_chats(self):
        with self._transaction(write=False) as conn:
            return _rows(conn.execute('SELECT id,title,version FROM chats ORDER BY rowid DESC LIMIT 100'))

    def messages(self, chat_id: str, *, before: str | None = None, limit: int = 100):
        """Latest `limit` messages in chronological order; `before` pages older history.

        `before` is the id of the oldest message a client already has. Without
        arguments the result is exactly the M1 contract (latest 100).
        """
        _uuid(chat_id)
        if before is not None:
            _uuid(before)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreFailure('INVALID_INPUT')
        with self._transaction(write=False) as conn:
            if conn.execute('SELECT 1 FROM chats WHERE id=?', (chat_id,)).fetchone() is None:
                raise StoreFailure('NOT_FOUND')
            upper = 9223372036854775807
            if before is not None:
                row = conn.execute('SELECT sequence FROM messages WHERE id=? AND chat_id=?', (before, chat_id)).fetchone()
                if row is None:
                    raise StoreFailure('NOT_FOUND')
                upper = row[0]
            return _rows(conn.execute('''SELECT id,chat_id,role,content,status,version,operation_id,created_at
                FROM (SELECT * FROM messages WHERE chat_id=? AND sequence<? ORDER BY sequence DESC LIMIT ?)
                ORDER BY sequence''', (chat_id, upper, limit)))

    def begin_generation(self, operation_id: str, chat_id: str, content: str, expected_version: int):
        _uuid(operation_id)
        _uuid(chat_id)
        _bounded(content, 32768)
        if type(expected_version) is not int or not 1 <= expected_version <= 9223372036854775807:
            raise StoreFailure('INVALID_INPUT')
        digest = _digest('begin_generation', {'chat_id': chat_id, 'content': content, 'expected_version': expected_version})
        with self._transaction() as conn:
            replay = self._replay(conn, operation_id, digest)
            if replay is not None:
                return replay
            chat = conn.execute('SELECT version FROM chats WHERE id=?', (chat_id,)).fetchone()
            if chat is None:
                raise StoreFailure('NOT_FOUND')
            if chat[0] != expected_version:
                raise StoreFailure('VERSION_CONFLICT')
            if conn.execute("SELECT 1 FROM generations WHERE chat_id=? AND state IN ('queued','started','streaming')", (chat_id,)).fetchone():
                raise StoreFailure('GENERATION_BUSY')
            generation_id = str(uuid4())
            conn.execute('INSERT INTO operation_journal(operation_id,kind,digest,status,result) VALUES(?,?,?,?,?)',
                         (operation_id, 'begin_generation', digest, 'queued', '{}'))
            conn.execute('INSERT INTO messages(id,chat_id,operation_id,role,content,status) VALUES(?,?,?,?,?,?)',
                         (str(uuid4()), chat_id, operation_id, 'user', content, 'completed'))
            conn.execute('INSERT INTO generations(id,operation_id,chat_id,state) VALUES(?,?,?,?)',
                         (generation_id, operation_id, chat_id, 'queued'))
            conn.execute('UPDATE chats SET version=version+1 WHERE id=?', (chat_id,))
            result = self._generation(conn, generation_id)
            conn.execute('UPDATE operation_journal SET result=? WHERE operation_id=?', (json.dumps(result), operation_id))
            self._event(conn, 'generation.queued', chat_id, generation_id, result)
            return result

    def begin_regeneration(self, operation_id: str, chat_id: str, expected_version: int):
        """Stage a replacement. Prior answer is kept until new one completes."""
        _uuid(operation_id); _uuid(chat_id)
        if type(expected_version) is not int or expected_version < 1:
            raise StoreFailure('INVALID_INPUT')
        digest = _digest('regenerate_generation', {'chat_id':chat_id, 'expected_version':expected_version})
        with self._transaction() as conn:
            replay = self._replay(conn, operation_id, digest)
            if replay is not None: return replay
            chat = conn.execute('SELECT version FROM chats WHERE id=?',(chat_id,)).fetchone()
            if chat is None: raise StoreFailure('NOT_FOUND')
            if chat[0] != expected_version: raise StoreFailure('VERSION_CONFLICT')
            if conn.execute("SELECT 1 FROM generations WHERE chat_id=? AND state IN ('queued','started','streaming')",(chat_id,)).fetchone():
                raise StoreFailure('GENERATION_BUSY')
            tail=conn.execute('SELECT id,role,status,operation_id FROM messages WHERE chat_id=? ORDER BY sequence DESC LIMIT 2',(chat_id,)).fetchall()
            if len(tail)!=2 or tuple(tail[0][1:3]) != ('assistant','completed') or tail[1][1]!='user':
                raise StoreFailure('NOTHING_TO_REGENERATE')
            generation_id=str(uuid4())
            conn.execute('INSERT INTO operation_journal(operation_id,kind,digest,status,result) VALUES(?,?,?,?,?)',
                         (operation_id,'regenerate_generation',digest,'queued','{}'))
            conn.execute('INSERT INTO generations(id,operation_id,chat_id,state) VALUES(?,?,?,?)',
                         (generation_id,operation_id,chat_id,'queued'))
            conn.execute('INSERT INTO regeneration_targets(generation_id,message_id) VALUES(?,?)',(generation_id,tail[0][0]))
            result=self._generation(conn,generation_id)
            conn.execute('UPDATE operation_journal SET result=? WHERE operation_id=?',(json.dumps(result),operation_id))
            self._event(conn,'generation.queued',chat_id,generation_id,result)
            return result

    def begin_continuation(self, operation_id: str, chat_id: str, expected_version: int):
        """Stage appended text; original assistant reply survives stop/failure."""
        _uuid(operation_id); _uuid(chat_id)
        if type(expected_version) is not int or expected_version < 1:
            raise StoreFailure('INVALID_INPUT')
        digest=_digest('continue_generation', {'chat_id':chat_id,'expected_version':expected_version})
        with self._transaction() as conn:
            replay=self._replay(conn,operation_id,digest)
            if replay is not None: return replay
            chat=conn.execute('SELECT version FROM chats WHERE id=?',(chat_id,)).fetchone()
            if chat is None: raise StoreFailure('NOT_FOUND')
            if chat[0]!=expected_version: raise StoreFailure('VERSION_CONFLICT')
            if conn.execute("SELECT 1 FROM generations WHERE chat_id=? AND state IN ('queued','started','streaming')",(chat_id,)).fetchone():
                raise StoreFailure('GENERATION_BUSY')
            tail=conn.execute('SELECT id,role,status,content FROM messages WHERE chat_id=? ORDER BY sequence DESC LIMIT 2',(chat_id,)).fetchall()
            if len(tail)!=2 or tuple(tail[0][1:3])!=('assistant','completed') or tail[1][1]!='user':
                raise StoreFailure('NOTHING_TO_CONTINUE')
            if len(tail[0][3])>=260000: raise StoreFailure('OUTPUT_LIMIT_EXCEEDED')
            identity=str(uuid4())
            conn.execute('INSERT INTO operation_journal(operation_id,kind,digest,status,result) VALUES(?,?,?,?,?)',
                         (operation_id,'continue_generation',digest,'queued','{}'))
            conn.execute('INSERT INTO generations(id,operation_id,chat_id,state) VALUES(?,?,?,?)',
                         (identity,operation_id,chat_id,'queued'))
            conn.execute('INSERT INTO continuation_targets(generation_id,message_id) VALUES(?,?)',(identity,tail[0][0]))
            result=self._generation(conn,identity)
            conn.execute('UPDATE operation_journal SET result=? WHERE operation_id=?',(json.dumps(result),operation_id))
            self._event(conn,'generation.queued',chat_id,identity,result)
            return result

    def continuation_context(self, generation_id: str):
        """Original assistant reply is context, but not a new user message in DB."""
        _uuid(generation_id)
        with self._transaction(write=False) as conn:
            current=self._generation(conn,generation_id)
            if not conn.execute('SELECT 1 FROM continuation_targets WHERE generation_id=?',(generation_id,)).fetchone():
                raise StoreFailure('NOT_CONTINUATION')
        rows=self.messages(current['chat_id'])
        rows.append({'role':'user','status':'persisted',
                     'content':'Продолжи непосредственно с места остановки предыдущего ответа. Не повторяй уже написанное. Не пиши за пользователя.'})
        return rows

    def generation_context(self, generation_id: str):
        """Exclude superseded AI reply during replacement inference."""
        _uuid(generation_id)
        with self._transaction(write=False) as conn:
            current=self._generation(conn,generation_id)
            target=conn.execute('SELECT message_id FROM regeneration_targets WHERE generation_id=?',(generation_id,)).fetchone()
            rows=_rows(conn.execute("""SELECT id,chat_id,role,content,status,version,operation_id,created_at
                FROM (SELECT * FROM messages WHERE chat_id=? AND id != ? ORDER BY sequence DESC LIMIT 100)
                ORDER BY sequence""",(current['chat_id'],target[0] if target else '')))
            return rows

    def generation(self, generation_id: str):
        _uuid(generation_id)
        with self._transaction(write=False) as conn:
            return self._generation(conn, generation_id)

    def operation(self, operation_id: str):
        _uuid(operation_id)
        with self._transaction(write=False) as conn:
            return self._operation(conn, operation_id)

    def request_stop(self, operation_id: str, generation_id: str):
        """Persist an idempotent stop request; only the worker finalizes text."""
        _uuid(operation_id)
        _uuid(generation_id)
        digest = _digest('stop_generation', {'generation_id': generation_id})
        with self._transaction() as conn:
            replay = self._replay(conn, operation_id, digest)
            if replay is not None:
                return replay
            generation = self._generation(conn, generation_id)
            if generation['state'] not in {'completed', 'stopped', 'failed'}:
                conn.execute('UPDATE generations SET stop_requested=1,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                             (generation_id,))
                generation = self._generation(conn, generation_id)
                # The generation journal's snapshot must reflect the durable
                # stop flag without changing its current execution status.
                conn.execute('UPDATE operation_journal SET result=? WHERE operation_id=?',
                             (json.dumps(generation), generation['operation_id']))
            conn.execute('INSERT INTO operation_journal(operation_id,kind,digest,status,result) VALUES(?,?,?,?,?)',
                         (operation_id, 'stop_generation', digest, 'completed', json.dumps(generation)))
            self._event(conn, 'generation.stop_requested', generation['chat_id'], generation_id, generation)
            return generation

    def record_control(self, operation_id: str, kind: str):
        _uuid(operation_id)
        if kind != 'shutdown':
            raise StoreFailure('INVALID_INPUT')
        digest = _digest('shutdown', {})
        with self._transaction() as conn:
            replay = self._replay(conn, operation_id, digest)
            if replay is not None:
                return replay
            receipt = {'operation_id': operation_id, 'kind': kind, 'status': 'completed'}
            conn.execute('INSERT INTO operation_journal(operation_id,kind,digest,status,result) VALUES(?,?,?,?,?)',
                         (operation_id, kind, digest, 'completed', json.dumps(receipt)))
            self._event(conn, 'control.shutdown', None, None, receipt)
            return receipt

    def set_generation_state(self, generation_id: str, state: str, *, text='', error_code=None):
        _uuid(generation_id)
        _bounded(text, 262144, empty=True)
        if (not isinstance(state, str) or state not in {'queued', 'started', 'streaming', 'completed', 'stopped', 'failed'}
                or (error_code is not None and (not isinstance(error_code, str) or re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', error_code) is None))
                or (error_code is not None and state != 'failed')):
            raise StoreFailure('INVALID_INPUT')
        with self._transaction() as conn:
            return self._set_state(conn, generation_id, state, text, error_code)

    def _set_state(self, conn, generation_id, state, text, error_code):
        current = self._generation(conn, generation_id)
        if current['state'] in {'completed', 'stopped', 'failed'}:
            if (current['state'], current['text'], current['error_code']) == (state, text, error_code):
                return current
            raise StoreFailure('TERMINAL_STATE_IMMUTABLE')
        allowed = {
            'queued': {'started', 'stopped', 'failed'},
            'started': {'streaming', 'completed', 'stopped', 'failed'},
            'streaming': {'streaming', 'completed', 'stopped', 'failed'},
        }
        if state not in allowed[current['state']]:
            raise StoreFailure('INVALID_STATE_TRANSITION')
        conn.execute('UPDATE generations SET state=?,text=?,error_code=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                     (state, text, error_code, generation_id))
        if state == 'streaming' and current['state'] == 'streaming':
            # Draft-only update: the snapshot row is the source of truth for
            # SSE/polling. Skipping the journal/event rewrite avoids O(n^2)
            # writes and one durable event row per streamed fragment.
            return self._generation(conn, generation_id)
        target = conn.execute('SELECT message_id FROM regeneration_targets WHERE generation_id=?',
                              (generation_id,)).fetchone()
        continuation=conn.execute('SELECT message_id FROM continuation_targets WHERE generation_id=?',(generation_id,)).fetchone()
        if target and state == 'completed':
            # Replacement commits atomically with the new message.
            conn.execute('DELETE FROM regeneration_targets WHERE generation_id=?',(generation_id,))
            conn.execute('DELETE FROM messages WHERE id=? AND chat_id=?', (target[0],current['chat_id']))
        if continuation and state=='completed':
            original=conn.execute('SELECT content FROM messages WHERE id=?',(continuation[0],)).fetchone()
            if original is None:
                raise StoreFailure('OUTPUT_LIMIT_EXCEEDED')
            # Some models echo the last phrase of the supplied assistant
            # context. Drop only a sufficiently long exact overlap; short
            # matching words might be intentional and are left intact.
            prior=original[0]
            overlap=0
            for amount in range(min(len(prior),len(text),2048),7,-1):
                if prior.endswith(text[:amount]):
                    overlap=amount
                    break
            combined=prior+text[overlap:]
            if len(combined)>262144:
                raise StoreFailure('OUTPUT_LIMIT_EXCEEDED')
            conn.execute('UPDATE messages SET content=?,version=version+1 WHERE id=?',(combined,continuation[0]))
            conn.execute('DELETE FROM continuation_targets WHERE generation_id=?',(generation_id,))
            conn.execute('UPDATE chats SET version=version+1 WHERE id=?',(current['chat_id'],))
        if continuation and state in ('stopped','failed'):
            conn.execute('DELETE FROM continuation_targets WHERE generation_id=?',(generation_id,))
        if not continuation and (state == 'completed' or (state == 'stopped' and text and not target)):
            conn.execute('INSERT INTO messages(id,chat_id,operation_id,role,content,status) VALUES(?,?,?,?,?,?)',
                         (str(uuid4()), current['chat_id'], current['operation_id'], 'assistant', text, state))
            conn.execute('UPDATE chats SET version=version+1 WHERE id=?', (current['chat_id'],))
        result = self._generation(conn, generation_id)
        conn.execute('UPDATE operation_journal SET status=?,result=? WHERE operation_id=?',
                     (state, json.dumps(result), current['operation_id']))
        self._event(conn, 'generation.' + state, current['chat_id'], generation_id, result)
        return result

    def events(self, after: int, limit: int = 100):
        if type(after) is not int or not 0 <= after <= 9223372036854775807 or type(limit) is not int or not 1 <= limit <= 100:
            raise StoreFailure('INVALID_INPUT')
        with self._transaction(write=False) as conn:
            events = _rows(conn.execute('SELECT sequence,type,chat_id,generation_id,payload FROM events WHERE sequence>? ORDER BY sequence LIMIT ?', (after, limit)))
            for event in events:
                event['payload'] = json.loads(event['payload'])
            return events

    # ---- characters and worlds (M1.7, decision D2) ---------------------------
    _RECORD_TABLES = {'character': 'characters', 'world': 'worlds'}

    @staticmethod
    def _record_id(record_id):
        if not isinstance(record_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', record_id) is None:
            raise StoreFailure('INVALID_ID')

    @staticmethod
    def _record_data(data):
        """Client-defined JSON object; bounded, named, never carries the avatar."""
        if not isinstance(data, dict):
            raise StoreFailure('INVALID_INPUT')
        data = {k: v for k, v in data.items() if k != 'avatar'}
        name = data.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise StoreFailure('INVALID_INPUT')
        try:
            encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            raise StoreFailure('INVALID_INPUT') from None
        if len(encoded.encode('utf-8')) > MAX_RECORD_BYTES:
            raise StoreFailure('INVALID_INPUT')
        return encoded

    def _table(self, kind):
        if kind not in self._RECORD_TABLES:
            raise StoreFailure('INVALID_INPUT')
        return self._RECORD_TABLES[kind]

    def list_records(self, kind):
        table = self._table(kind)
        with self._transaction(write=False) as conn:
            if kind == 'character':
                # `avatar` is a cheap fingerprint of the stored picture (size + last bytes): clients compare it to
                # notice an avatar added, changed or removed on another device (2026-11-08).
                rows = conn.execute('''SELECT c.id, c.version, c.data, a.media_type IS NOT NULL,
                    CASE WHEN a.data IS NULL THEN '' ELSE length(a.data) || ':' || hex(substr(a.data, -24)) END
                    FROM characters c LEFT JOIN character_avatars a ON a.character_id = c.id ORDER BY c.id''').fetchall()
                return [{'id': i, 'version': v, 'data': json.loads(d), 'has_avatar': bool(h), 'avatar': f} for i, v, d, h, f in rows]
            rows = conn.execute(f'SELECT id, version, data FROM {table} ORDER BY id').fetchall()
            return [{'id': i, 'version': v, 'data': json.loads(d)} for i, v, d in rows]

    def get_record(self, kind, record_id):
        self._record_id(record_id)
        table = self._table(kind)
        with self._transaction(write=False) as conn:
            row = conn.execute(f'SELECT id, version, data FROM {table} WHERE id=?', (record_id,)).fetchone()
        if row is None:
            raise StoreFailure('NOT_FOUND')
        return {'id': row[0], 'version': row[1], 'data': json.loads(row[2])}

    def put_record(self, kind, record_id, data, expected_version=None):
        """Create or replace. expected_version: None = last write wins, 0 = must not exist."""
        self._record_id(record_id)
        table = self._table(kind)
        encoded = self._record_data(data)
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise StoreFailure('INVALID_INPUT')
        with self._transaction() as conn:
            row = conn.execute(f'SELECT version, data FROM {table} WHERE id=?', (record_id,)).fetchone()
            current = row[0] if row else 0
            if expected_version is not None and expected_version != current:
                raise StoreFailure('VERSION_CONFLICT')
            if row is not None and row[1] == encoded:
                return {'id': record_id, 'version': current}  # Unchanged: idempotent replay.
            if row is None:
                count = conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                if count >= MAX_RECORDS[kind]:
                    raise StoreFailure('RECORD_LIMIT')
                conn.execute(f'INSERT INTO {table}(id, version, data) VALUES(?,?,?)', (record_id, 1, encoded))
            else:
                conn.execute(f'UPDATE {table} SET version=version+1, data=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                             (encoded, record_id))
            self._event(conn, kind + '.updated', None, None, {'id': record_id})
            return {'id': record_id, 'version': current + 1}

    def delete_record(self, kind, record_id, expected_version=None):
        self._record_id(record_id)
        table = self._table(kind)
        with self._transaction() as conn:
            row = conn.execute(f'SELECT version FROM {table} WHERE id=?', (record_id,)).fetchone()
            if row is None:
                raise StoreFailure('NOT_FOUND')
            if expected_version is not None and expected_version != row[0]:
                raise StoreFailure('VERSION_CONFLICT')
            conn.execute(f'DELETE FROM {table} WHERE id=?', (record_id,))
            self._event(conn, kind + '.deleted', None, None, {'id': record_id})
            return {'id': record_id, 'deleted': True}

    def put_avatar(self, character_id, media_type, data):
        self._record_id(character_id)
        data = bytes(data) if isinstance(data, (bytes, bytearray)) else None
        if (media_type not in AVATAR_SIGNATURES or data is None or not 0 < len(data) <= MAX_AVATAR_BYTES
                or not data.startswith(AVATAR_SIGNATURES[media_type])
                or (media_type == 'image/webp' and data[8:12] != b'WEBP')):
            raise StoreFailure('INVALID_INPUT')
        with self._transaction() as conn:
            if conn.execute('SELECT 1 FROM characters WHERE id=?', (character_id,)).fetchone() is None:
                raise StoreFailure('NOT_FOUND')
            conn.execute('''INSERT INTO character_avatars(character_id, media_type, data) VALUES(?,?,?)
                ON CONFLICT(character_id) DO UPDATE SET media_type=excluded.media_type, data=excluded.data''',
                         (character_id, media_type, data))
            return {'id': character_id, 'media_type': media_type, 'bytes': len(data)}

    def get_avatar(self, character_id):
        self._record_id(character_id)
        with self._transaction(write=False) as conn:
            row = conn.execute('SELECT media_type, data FROM character_avatars WHERE character_id=?',
                               (character_id,)).fetchone()
        if row is None:
            raise StoreFailure('NOT_FOUND')
        return row[0], bytes(row[1])

    def delete_avatar(self, character_id):
        self._record_id(character_id)
        with self._transaction() as conn:
            if conn.execute('DELETE FROM character_avatars WHERE character_id=?', (character_id,)).rowcount == 0:
                raise StoreFailure('NOT_FOUND')
            return {'id': character_id, 'deleted': True}

    def put_media_image(self, media_type, data, params):
        data = bytes(data) if isinstance(data, (bytes, bytearray)) else None
        if (media_type not in AVATAR_SIGNATURES or data is None or not 0 < len(data) <= 32 * 1024 * 1024
                or not data.startswith(AVATAR_SIGNATURES[media_type])):
            raise StoreFailure('INVALID_INPUT')
        image_id = uuid4().hex
        with self._transaction() as conn:
            conn.execute('INSERT INTO media_images(id, media_type, data, params) VALUES(?,?,?,?)',
                         (image_id, media_type, data, json.dumps(params, ensure_ascii=False)))
        return image_id

    def list_media_images(self, limit=200):
        with self._transaction(write=False) as conn:
            rows = conn.execute('SELECT id, params, created_at, length(data) FROM media_images '
                                'ORDER BY created_at DESC, rowid DESC LIMIT ?', (limit,)).fetchall()
        return [{'id': r[0], 'params': json.loads(r[1]), 'created_at': r[2], 'bytes': r[3]} for r in rows]

    def delete_media_image(self, image_id):
        if not isinstance(image_id, str) or not image_id.isalnum() or len(image_id) > 64:
            raise StoreFailure('NOT_FOUND')
        with self._transaction() as conn:
            if conn.execute('DELETE FROM media_images WHERE id=?', (image_id,)).rowcount == 0:
                raise StoreFailure('NOT_FOUND')
        return {'id': image_id, 'deleted': True}

    def get_media_image(self, image_id):
        if not isinstance(image_id, str) or not image_id.isalnum() or len(image_id) > 64:
            raise StoreFailure('NOT_FOUND')
        with self._transaction(write=False) as conn:
            row = conn.execute('SELECT media_type, data FROM media_images WHERE id=?', (image_id,)).fetchone()
        if row is None:
            raise StoreFailure('NOT_FOUND')
        return row[0], bytes(row[1])

    # ---- user TTS voices (2026-11-08) ----------------------------------------
    def put_tts_voice(self, meta, data):
        if not isinstance(meta, dict) or not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= 6 * 1024 * 1024:
            raise StoreFailure('INVALID_INPUT')
        voice_id = 'u' + uuid4().hex[:20]
        with self._transaction() as conn:
            conn.execute('INSERT INTO tts_voices(id, meta, data) VALUES(?,?,?)',
                         (voice_id, json.dumps(meta, ensure_ascii=False), bytes(data)))
        return voice_id

    def list_tts_voices(self):
        with self._transaction(write=False) as conn:
            rows = conn.execute('SELECT id, meta, created_at FROM tts_voices ORDER BY created_at, rowid').fetchall()
        return [{**json.loads(m), 'id': i, 'created_at': c} for i, m, c in rows]

    def get_tts_voice(self, voice_id):
        if not isinstance(voice_id, str) or not voice_id.isalnum() or len(voice_id) > 64:
            raise StoreFailure('NOT_FOUND')
        with self._transaction(write=False) as conn:
            row = conn.execute('SELECT meta, data FROM tts_voices WHERE id=?', (voice_id,)).fetchone()
        if row is None:
            raise StoreFailure('NOT_FOUND')
        return json.loads(row[0]), bytes(row[1])

    def delete_tts_voice(self, voice_id):
        """Removes only the voice; characters keep their settings and fall back to the default voice."""
        if not isinstance(voice_id, str) or not voice_id.isalnum() or len(voice_id) > 64:
            raise StoreFailure('NOT_FOUND')
        with self._transaction() as conn:
            if conn.execute('DELETE FROM tts_voices WHERE id=?', (voice_id,)).rowcount == 0:
                raise StoreFailure('NOT_FOUND')
        return {'id': voice_id, 'deleted': True}

    def set_generation_meta(self, generation_id: str, finish_reason, completion_tokens=None):
        """Record why a reply ended; 'length' means it was cut by the token limit."""
        _uuid(generation_id)
        if finish_reason is not None and (not isinstance(finish_reason, str) or len(finish_reason) > 32):
            raise StoreFailure('INVALID_INPUT')
        if completion_tokens is not None and (type(completion_tokens) is not int or completion_tokens < 0):
            completion_tokens = None
        with self._transaction() as conn:
            conn.execute('''INSERT INTO generation_meta(generation_id, finish_reason, completion_tokens) VALUES(?,?,?)
                ON CONFLICT(generation_id) DO UPDATE SET finish_reason=excluded.finish_reason,
                completion_tokens=excluded.completion_tokens''', (generation_id, finish_reason, completion_tokens))

    # ---- chat metadata and deletion (2026-11-06) ------------------------------------
    def chats_with_meta(self):
        """Chats (newest first, up to 500) with client metadata and message counts."""
        with self._transaction(write=False) as conn:
            rows = conn.execute('''SELECT c.id, c.title, c.version, c.created_at,
                (SELECT COUNT(*) FROM messages m WHERE m.chat_id=c.id) AS messages,
                (SELECT MAX(created_at) FROM messages m WHERE m.chat_id=c.id) AS last_message_at,
                (SELECT data FROM chat_meta x WHERE x.chat_id=c.id) AS meta
                FROM chats c ORDER BY c.rowid DESC LIMIT 500''').fetchall()
        out = []
        for chat_id, title, version, created, count, last, meta in rows:
            try:
                meta = json.loads(meta) if meta else {}
            except ValueError:
                meta = {}
            out.append({'id': chat_id, 'title': title, 'version': version, 'created_at': created,
                        'messages': count, 'last_message_at': last, 'meta': meta})
        return out

    def set_chat_meta(self, chat_id: str, data, title=None):
        _uuid(chat_id)
        if not isinstance(data, dict):
            raise StoreFailure('INVALID_INPUT')
        text = json.dumps(data, ensure_ascii=False)
        if len(text) > 65536:
            raise StoreFailure('INVALID_INPUT')
        if title is not None:
            _bounded(title, 200)
        with self._transaction() as conn:
            if conn.execute('SELECT 1 FROM chats WHERE id=?', (chat_id,)).fetchone() is None:
                raise StoreFailure('NOT_FOUND')
            conn.execute('''INSERT INTO chat_meta(chat_id, data) VALUES(?,?) ON CONFLICT(chat_id)
                DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP''', (chat_id, text))
            if title is not None:
                conn.execute('UPDATE chats SET title=? WHERE id=?', (title, chat_id))
        return {'chat_id': chat_id, 'meta': data, 'title': title}

    def delete_chat(self, chat_id: str):
        """Remove a chat and everything derived from it (messages, replies, memory, settings)."""
        _uuid(chat_id)
        with self._transaction() as conn:
            if conn.execute('SELECT 1 FROM chats WHERE id=?', (chat_id,)).fetchone() is None:
                missing = True
            else:
                missing = False
                busy = conn.execute("SELECT 1 FROM generations WHERE chat_id=? AND state IN ('queued','started','streaming')",
                                    (chat_id,)).fetchone() is not None
                if not busy:
                    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    generations = [r[0] for r in conn.execute('SELECT id FROM generations WHERE chat_id=?', (chat_id,))]
                    operations = [r[0] for r in conn.execute('SELECT operation_id FROM generations WHERE chat_id=?', (chat_id,))]
                    for table in ('regeneration_targets', 'continuation_targets', 'generation_meta'):
                        if table in tables:
                            conn.executemany(f'DELETE FROM {table} WHERE generation_id=?', [(g,) for g in generations])
                    conn.execute('DELETE FROM events WHERE chat_id=? OR generation_id IN (SELECT id FROM generations WHERE chat_id=?)',
                                 (chat_id, chat_id))
                    conn.execute('DELETE FROM messages WHERE chat_id=?', (chat_id,))
                    conn.execute('DELETE FROM generations WHERE chat_id=?', (chat_id,))
                    conn.executemany('DELETE FROM operation_journal WHERE operation_id=?', [(o,) for o in operations])
                    # Journal rows that only describe this chat (create/rename) carry its id in the result.
                    conn.execute('DELETE FROM operation_journal WHERE result LIKE ?', ('%' + chat_id + '%',))
                    for table in ('chat_settings', 'chat_meta', 'chat_memory', 'chat_memory_state'):
                        if table in tables:
                            conn.execute(f'DELETE FROM {table} WHERE chat_id=?', (chat_id,))
                    conn.execute('DELETE FROM chats WHERE id=?', (chat_id,))
        if missing:
            raise StoreFailure('NOT_FOUND')
        if busy:
            raise StoreFailure('GENERATION_BUSY')
        return {'chat_id': chat_id, 'deleted': True}

    # ---- per-chat settings (stage 4a) ------------------------------------------
    def chat_settings(self, chat_id: str):
        _uuid(chat_id)
        with self._transaction(write=False) as conn:
            if conn.execute('SELECT 1 FROM chats WHERE id=?', (chat_id,)).fetchone() is None:
                raise StoreFailure('NOT_FOUND')
            row = conn.execute('SELECT preset_id FROM chat_settings WHERE chat_id=?', (chat_id,)).fetchone()
        return {'chat_id': chat_id, 'preset_id': row[0] if row else None}

    def set_chat_settings(self, chat_id: str, preset_id):
        """preset_id None = use the server profile (default)."""
        _uuid(chat_id)
        if preset_id is not None and (not isinstance(preset_id, str) or re.fullmatch(r'[a-z0-9-]{1,64}', preset_id) is None):
            raise StoreFailure('INVALID_INPUT')
        with self._transaction() as conn:
            if conn.execute('SELECT 1 FROM chats WHERE id=?', (chat_id,)).fetchone() is None:
                raise StoreFailure('NOT_FOUND')
            if preset_id is None:
                conn.execute('DELETE FROM chat_settings WHERE chat_id=?', (chat_id,))
            else:
                conn.execute('''INSERT INTO chat_settings(chat_id, preset_id) VALUES(?,?)
                    ON CONFLICT(chat_id) DO UPDATE SET preset_id=excluded.preset_id, updated_at=CURRENT_TIMESTAMP''',
                             (chat_id, preset_id))
            self._event(conn, 'chat.settings', chat_id, None, {'preset_id': preset_id})
        return {'chat_id': chat_id, 'preset_id': preset_id}

    def recover_interrupted(self):
        with self._transaction() as conn:
            pending = conn.execute("SELECT id,text FROM generations WHERE state IN ('queued','started','streaming') ORDER BY rowid").fetchall()
            for generation_id, text in pending:
                self._set_state(conn, generation_id, 'failed', text, 'SERVER_RESTARTED')
            return len(pending)
