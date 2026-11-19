"""Fail-closed SQLCipher experiment. No sqlite3 fallback."""
import os
from pathlib import Path


class StorageFailure(RuntimeError):
    pass


def open_database(path: Path, key: bytes, driver=None):
    if not isinstance(key, bytes) or len(key) != 32:
        raise StorageFailure('INVALID_KEY')
    if driver is None:
        try:
            from sqlcipher3 import dbapi2 as driver
        except ImportError:
            raise StorageFailure('SQLCIPHER_UNAVAILABLE') from None
    # Verify the binding before even creating a disk file. SQLite silently
    # ignores cipher pragmas, so successful connection is not evidence.
    probe = driver.connect(':memory:')
    try:
        row = probe.execute('PRAGMA cipher_version').fetchone()
        if not row or not row[0]:
            raise StorageFailure('SQLCIPHER_UNAVAILABLE')
    finally:
        probe.close()
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    c = driver.connect(str(path))
    try:
        c.execute('PRAGMA key = "x\'' + key.hex() + '\'"')
        c.execute('PRAGMA cipher_memory_security = ON')
        c.execute('PRAGMA temp_store = MEMORY')
        c.execute('PRAGMA foreign_keys = ON')
        c.execute('PRAGMA secure_delete = ON')
        c.execute('PRAGMA trusted_schema = OFF')
        c.execute('SELECT count(*) FROM sqlite_master').fetchone()
        c.execute('PRAGMA journal_mode = WAL')
        return c
    except driver.DatabaseError:
        c.close()
        raise StorageFailure('DATABASE_LOCKED_OR_INVALID') from None
