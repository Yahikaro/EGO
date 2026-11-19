"""Accounts, login and sessions.

The owner creates accounts on the PC (Control Center -> Access): an email-style login and a
password; more accounts can be added for friends. Nothing is sent by mail. The PC itself opens
the client with a one-time launch code; other devices sign in with login + password.
Passwords and session tokens are stored only as hashes inside the encrypted main database; they
never reach logs, events or diagnostics.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid

from server.ego_core.service import CoreFailure

PASSWORD_ATTEMPTS = 10         # wrong passwords before a 1 hour lock
PASSWORD_LOCK = 3600.0
SESSION_TTL = 7 * 86400.0      # sliding window: a lost phone stays signed in less
SESSION_TOUCH = 3600.0         # extend expiry at most once per hour
MAX_SESSIONS = 20
JOURNAL_KEEP = 200
RATE_WINDOW, RATE_LIMIT = 60.0, 30   # unauthenticated account requests per minute
PASSWORD_MIN, PASSWORD_MAX = 10, 256
PRONOUNS = {'he', 'she', 'they', 'other'}
ACCENTS = {'violet', 'blue', 'rose', 'mono'}
_EMAIL = re.compile(r'[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63})+')
_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
        failed_logins INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS user_profiles(user_id TEXT PRIMARY KEY, data TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS account_sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        created_at REAL NOT NULL, last_seen REAL NOT NULL, expires_at REAL NOT NULL, device TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS login_journal(sequence INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
        event TEXT NOT NULL, device TEXT NOT NULL, address TEXT NOT NULL)''',
)


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, **_SCRYPT)
    return 'scrypt$%d$%d$%d$%s$%s' % (_SCRYPT['n'], _SCRYPT['r'], _SCRYPT['p'],
                                      base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def check_password(password, stored):
    try:
        kind, n, r, p, salt, digest = stored.split('$')
        if kind != 'scrypt':
            return False
        actual = hashlib.scrypt(password.encode('utf-8'), salt=base64.b64decode(salt), n=int(n), r=int(r),
                                p=int(p), dklen=32, maxmem=_SCRYPT['maxmem'])
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password('ego-dummy-password-for-timing')


def _token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def normalize_email(email):
    if not isinstance(email, str):
        raise CoreFailure('ACCOUNT_INVALID_EMAIL')
    email = email.strip().lower()
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        raise CoreFailure('ACCOUNT_INVALID_EMAIL')
    return email


def check_new_password(password):
    if not isinstance(password, str) or not (PASSWORD_MIN <= len(password) <= PASSWORD_MAX) or not password.strip():
        raise CoreFailure('ACCOUNT_WEAK_PASSWORD')


def clean_profile(data):
    if not isinstance(data, dict):
        raise CoreFailure('INVALID_INPUT')
    limits = {'display_name': 60, 'nickname': 40, 'pronouns_text': 40, 'description': 1000}
    allowed = set(limits) | {'pronouns', 'accent_colour'}
    if set(data) - allowed:
        raise CoreFailure('INVALID_INPUT')
    out = {}
    for key, limit in limits.items():
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > limit:
            raise CoreFailure('INVALID_INPUT')
        out[key] = value.strip()
    pronouns = data.get('pronouns', 'they')
    accent = data.get('accent_colour', 'violet')
    if pronouns not in PRONOUNS or accent not in ACCENTS:
        raise CoreFailure('INVALID_INPUT')
    out['pronouns'], out['accent_colour'] = pronouns, accent
    return out


_COLUMNS = ('id', 'email', 'password_hash', 'created_at', 'failed_logins', 'locked_until')


class Accounts:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock
        self._rate, self._rate_lock = [], threading.Lock()
        with store._transaction() as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    # ---- helpers -------------------------------------------------------------
    def throttle(self):
        """Global limit for unauthenticated account routes."""
        now = self.clock()
        with self._rate_lock:
            self._rate = [t for t in self._rate if t > now - RATE_WINDOW]
            if len(self._rate) >= RATE_LIMIT:
                raise CoreFailure('ACCOUNT_RATE_LIMITED')
            self._rate.append(now)

    def _journal(self, conn, event, device='', address=''):
        conn.execute('INSERT INTO login_journal(at,event,device,address) VALUES(?,?,?,?)',
                     (self.clock(), event, (device or '')[:120], (address or '')[:64]))
        conn.execute('DELETE FROM login_journal WHERE sequence <= (SELECT MAX(sequence) FROM login_journal) - ?',
                     (JOURNAL_KEEP,))

    def _user(self, conn, email=None):
        sql = 'SELECT id,email,password_hash,created_at,failed_logins,locked_until FROM users'
        row = conn.execute(sql + (' WHERE email=?' if email else ' ORDER BY created_at LIMIT 1'),
                           (email,) if email else ()).fetchone()
        return dict(zip(_COLUMNS, row)) if row else None

    def _user_by_id(self, conn, user_id):
        row = conn.execute('SELECT id,email,password_hash,created_at,failed_logins,locked_until FROM users WHERE id=?',
                           (user_id,)).fetchone()
        return dict(zip(_COLUMNS, row)) if row else None

    def _new_session(self, conn, user_id, device):
        token, now = secrets.token_urlsafe(32), self.clock()
        conn.execute('INSERT INTO account_sessions VALUES(?,?,?,?,?,?)',
                     (_token_hash(token), user_id, now, now, now + SESSION_TTL, (device or 'unknown device')[:120]))
        conn.execute('''DELETE FROM account_sessions WHERE user_id=? AND token_hash NOT IN
            (SELECT token_hash FROM account_sessions WHERE user_id=? ORDER BY last_seen DESC LIMIT ?)''',
                     (user_id, user_id, MAX_SESSIONS))
        return {'token': token, 'scope': 'account', 'expires_in': int(SESSION_TTL)}

    # ---- sign-in (any device) --------------------------------------------------
    def state(self):
        with self.store._transaction(write=False) as conn:
            count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        return {'account_exists': count > 0, 'accounts': count}

    def login(self, email, password, *, device='', address=''):
        try:
            email = normalize_email(email)
        except CoreFailure:
            raise CoreFailure('ACCOUNT_INVALID_CREDENTIALS') from None
        if not isinstance(password, str) or len(password) > PASSWORD_MAX:
            raise CoreFailure('ACCOUNT_INVALID_CREDENTIALS')
        with self.store._transaction(write=False) as conn:
            user = self._user(conn, email)
        if user is None:
            check_password(password, _DUMMY_HASH)          # same work as a real attempt
            raise CoreFailure('ACCOUNT_INVALID_CREDENTIALS')
        if user['locked_until'] > self.clock():
            raise CoreFailure('ACCOUNT_LOCKED')
        if not check_password(password, user['password_hash']):
            with self.store._transaction() as conn:
                failed = user['failed_logins'] + 1
                if failed >= PASSWORD_ATTEMPTS:
                    conn.execute('UPDATE users SET failed_logins=0, locked_until=? WHERE id=?',
                                 (self.clock() + PASSWORD_LOCK, user['id']))
                    self._journal(conn, 'locked_passwords', device, address)
                else:
                    conn.execute('UPDATE users SET failed_logins=? WHERE id=?', (failed, user['id']))
                    self._journal(conn, 'password_wrong', device, address)
            raise CoreFailure('ACCOUNT_LOCKED' if failed >= PASSWORD_ATTEMPTS else 'ACCOUNT_INVALID_CREDENTIALS')
        with self.store._transaction() as conn:
            conn.execute('UPDATE users SET failed_logins=0 WHERE id=?', (user['id'],))
            self._journal(conn, 'login', device, address)
            return self._new_session(conn, user['id'], device)

    def session_user(self, token):
        """User id for a valid session token (extends the sliding window), else None."""
        if not isinstance(token, str) or not (20 <= len(token) <= 128):
            return None
        digest, now = _token_hash(token), self.clock()
        with self.store._transaction(write=False) as conn:
            row = conn.execute('SELECT user_id, expires_at, last_seen FROM account_sessions WHERE token_hash=?',
                               (digest,)).fetchone()
        if row is None:
            return None
        if row[1] <= now:
            with self.store._transaction() as conn:
                conn.execute('DELETE FROM account_sessions WHERE token_hash=?', (digest,))
            return None
        if now - row[2] >= SESSION_TOUCH:
            with self.store._transaction() as conn:
                conn.execute('UPDATE account_sessions SET last_seen=?, expires_at=? WHERE token_hash=?',
                             (now, now + SESSION_TTL, digest))
        return row[0]

    def logout(self, token, *, everywhere=False, device='', address=''):
        user_id = self.session_user(token)
        if user_id is None:
            raise CoreFailure('UNAUTHORIZED')
        with self.store._transaction() as conn:
            if everywhere:
                conn.execute('DELETE FROM account_sessions WHERE user_id=?', (user_id,))
            else:
                conn.execute('DELETE FROM account_sessions WHERE token_hash=?', (_token_hash(token),))
            self._journal(conn, 'logout_all' if everywhere else 'logout', device, address)
        return {'status': 'signed_out', 'everywhere': everywhere}

    def logout_everywhere_local(self):
        """PC side: end every device session of every account."""
        with self.store._transaction() as conn:
            count = conn.execute('SELECT COUNT(*) FROM account_sessions').fetchone()[0]
            conn.execute('DELETE FROM account_sessions')
            self._journal(conn, 'logout_all', 'PC', 'local')
        return {'status': 'signed_out', 'everywhere': True, 'sessions': count}

    def me(self, token=None):
        """The signed-in account; on the PC (no session) the first one, if any."""
        current = _token_hash(token) if token else None
        user_id = self.session_user(token) if token else None
        with self.store._transaction(write=False) as conn:
            user = self._user_by_id(conn, user_id) if user_id else self._user(conn)
            if user is None:
                return {'account': None}
            row = conn.execute('SELECT data FROM user_profiles WHERE user_id=?', (user['id'],)).fetchone()
            profile = json.loads(row[0]) if row else clean_profile({})
            sessions = [{'device': d, 'created_at': c, 'last_seen': s, 'current': h == current}
                        for h, d, c, s in conn.execute('''SELECT token_hash, device, created_at, last_seen
                            FROM account_sessions WHERE user_id=? ORDER BY last_seen DESC''', (user['id'],))]
            journal = [{'at': a, 'event': e, 'device': d, 'address': ad} for a, e, d, ad in conn.execute(
                'SELECT at, event, device, address FROM login_journal ORDER BY sequence DESC LIMIT 20')]
        return {'account': {'email': user['email'], 'created_at': user['created_at'], 'profile': profile,
                            'sessions': sessions, 'journal': journal}}

    def set_profile(self, data, token=None):
        profile = clean_profile(data)
        user_id = self.session_user(token) if token else None
        with self.store._transaction() as conn:
            user = self._user_by_id(conn, user_id) if user_id else self._user(conn)
            if user is not None:
                conn.execute('''INSERT INTO user_profiles(user_id,data) VALUES(?,?)
                    ON CONFLICT(user_id) DO UPDATE SET data=excluded.data''', (user['id'], json.dumps(profile, ensure_ascii=False)))
        if user is None:
            raise CoreFailure('NOT_FOUND')
        return {'profile': profile}

    # ---- management (owner, on the PC, with the admin token) ---------------------
    def list_users(self):
        with self.store._transaction(write=False) as conn:
            rows = conn.execute('SELECT id,email,created_at,locked_until FROM users ORDER BY created_at').fetchall()
            out = []
            for user_id, email, created, locked in rows:
                sessions, last = conn.execute('SELECT COUNT(*), COALESCE(MAX(last_seen),0) FROM account_sessions WHERE user_id=?',
                                              (user_id,)).fetchone()
                out.append({'email': email, 'created_at': created, 'locked': locked > self.clock(),
                            'sessions': sessions, 'last_seen': last})
        return {'users': out}

    def add_user(self, email, password):
        email = normalize_email(email)
        check_new_password(password)
        exists = False
        with self.store._transaction() as conn:
            if self._user(conn, email) is not None:
                exists = True
            else:
                conn.execute('INSERT INTO users(id,email,password_hash,verified,created_at) VALUES(?,?,?,1,?)',
                             (str(uuid.uuid4()), email, hash_password(password), self.clock()))
                self._journal(conn, 'user_added', 'PC', 'local')
        if exists:                 # failures are raised after the transaction (inside it they become STORAGE_FAILURE)
            raise CoreFailure('ACCOUNT_EXISTS')
        return self.list_users()

    def remove_user(self, email):
        email = normalize_email(email)
        found = False
        with self.store._transaction() as conn:
            user = self._user(conn, email)
            if user is not None:
                found = True
                conn.execute('DELETE FROM account_sessions WHERE user_id=?', (user['id'],))
                conn.execute('DELETE FROM user_profiles WHERE user_id=?', (user['id'],))
                conn.execute('DELETE FROM users WHERE id=?', (user['id'],))
                self._journal(conn, 'user_removed', 'PC', 'local')
        if not found:
            raise CoreFailure('NOT_FOUND')
        return self.list_users()

    def set_user_password(self, email, password):
        email = normalize_email(email)
        check_new_password(password)
        found = False
        with self.store._transaction() as conn:
            user = self._user(conn, email)
            if user is not None:
                found = True
                conn.execute('UPDATE users SET password_hash=?, failed_logins=0, locked_until=0 WHERE id=?',
                             (hash_password(password), user['id']))
                conn.execute('DELETE FROM account_sessions WHERE user_id=?', (user['id'],))
                self._journal(conn, 'password_changed', 'PC', 'local')
        if not found:
            raise CoreFailure('NOT_FOUND')
        return self.list_users()
