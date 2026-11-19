"""Bounded synthetic snapshot container using library Scrypt + AES-256-GCM.
This versioned experiment is not the future application's backup format.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from server.ego_core.vault.crypto_restore.storage import open_database, StorageFailure

MAGIC = b'EGO-M0-BACKUP\x01'
MAX_BYTES = 16 * 1024 * 1024
DOMAINS = ('main', 'profile')


class BackupFailure(ValueError):
    pass


def derive(secret, salt):
    if not isinstance(secret, str) or not 32 <= len(secret) <= 1024:
        raise BackupFailure('RECOVERY_SECRET_REQUIRED')
    # Fixed resource limits: untrusted input can never choose KDF work factors.
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(secret.encode('utf-8'))


def snapshot(root: Path, keys, secret):
    if set(keys) != set(DOMAINS) or keys['main'] == keys['profile']:
        raise BackupFailure('INDEPENDENT_KEYS_REQUIRED')
    records = {}
    for name in DOMAINS:
        path = root / (name + '.db')
        if not path.is_file():
            raise BackupFailure('DATABASE_MISSING')
        wal = root / (name + '.db-wal')
        if wal.exists() and wal.stat().st_size:
            raise BackupFailure('CLOSE_DATABASES_BEFORE_SNAPSHOT')
        c = open_database(path, keys[name])
        try:
            if c.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise BackupFailure('DATABASE_INVALID')
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            c.close()
        if path.stat().st_size > MAX_BYTES // 3:
            raise BackupFailure('SPIKE_SIZE_LIMIT')
        records[name] = {'key': base64.b64encode(keys[name]).decode(),
                         'database': base64.b64encode(path.read_bytes()).decode()}
    plaintext = json.dumps({'version': 1, 'domains': records}, separators=(',', ':')).encode()
    if len(plaintext) > MAX_BYTES:
        raise BackupFailure('SPIKE_SIZE_LIMIT')
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce
    return header + AESGCM(derive(secret, salt)).encrypt(nonce, plaintext, header)


def restore(backup: bytes, secret, destination: Path):
    header_length = len(MAGIC) + 28
    if not header_length + 16 <= len(backup) <= MAX_BYTES + header_length + 16:
        raise BackupFailure('INVALID_BACKUP')
    if backup[:len(MAGIC)] != MAGIC:
        raise BackupFailure('UNSUPPORTED_BACKUP_VERSION')
    salt = backup[len(MAGIC):len(MAGIC)+16]
    nonce = backup[len(MAGIC)+16:header_length]
    try:
        plaintext = AESGCM(derive(secret, salt)).decrypt(nonce, backup[header_length:], backup[:header_length])
    except InvalidTag:
        raise BackupFailure('AUTHENTICATION_FAILED') from None
    # Validate everything in memory before touching the output directory.
    try:
        payload = json.loads(plaintext)
        if set(payload) != {'version', 'domains'} or payload['version'] != 1:
            raise ValueError()
        if set(payload['domains']) != set(DOMAINS):
            raise ValueError()
        values, keys = {}, {}
        for name in DOMAINS:
            item = payload['domains'][name]
            if set(item) != {'key', 'database'}:
                raise ValueError()
            keys[name] = base64.b64decode(item['key'], validate=True)
            values[name] = base64.b64decode(item['database'], validate=True)
            if len(keys[name]) != 32 or not values[name] or values[name].startswith(b'SQLite format 3'):
                raise ValueError()
        if keys['main'] == keys['profile']:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise BackupFailure('INVALID_PAYLOAD') from None
    if destination.exists():
        raise BackupFailure('DESTINATION_EXISTS')
    # Parent is a caller-owned synthetic directory. Lock coordinates this tool's
    # concurrent restorers; hostile processes with write access are out of scope.
    lock = destination.with_name(destination.name + '.restore-lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise BackupFailure('RESTORE_BUSY') from None
    os.close(fd)
    staging = None
    try:
        if destination.exists():
            raise BackupFailure('DESTINATION_EXISTS')
        staging = Path(tempfile.mkdtemp(prefix='.ego-m0-', dir=destination.parent))
        for name in DOMAINS:
            path = staging / (name + '.db')
            with path.open('xb') as f:
                path.chmod(0o600)
                f.write(values[name])
                f.flush()
                os.fsync(f.fileno())
            c = open_database(path, keys[name])
            try:
                if c.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise BackupFailure('DATABASE_INVALID')
                c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally:
                c.close()
        if destination.exists():
            raise BackupFailure('DESTINATION_EXISTS')
        staging.rename(destination)
        staging = None
        return keys
    except StorageFailure:
        raise BackupFailure('DATABASE_INVALID') from None
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        lock.unlink()
