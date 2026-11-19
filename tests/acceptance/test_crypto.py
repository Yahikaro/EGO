"""Breaks caught: plaintext fallback, key-domain mix-up, unauthenticated restore,
clobbered destination, plaintext sidecars and account-unbound key protection.
All data and recovery material are generated synthetic inputs.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

import pytest

from server.ego_core.vault.crypto_restore.storage import open_database, StorageFailure
from server.ego_core.vault.crypto_restore.backup import snapshot, restore, BackupFailure
from server.ego_core.vault.crypto_restore.dpapi import protect, unprotect


@pytest.fixture
def databases(tmp_path):
    try:
        __import__('sqlcipher3.dbapi2')
    except (ImportError, OSError):
        pytest.fail('SQLCIPHER_REQUIRED: run tools/windows/01_SETUP.bat; encryption tests cannot be skipped', pytrace=False)
    root = tmp_path / 'source'
    root.mkdir()
    keys = {name: secrets.token_bytes(32) for name in ('main', 'profile')}
    for name in keys:
        c = open_database(root / (name + '.db'), keys[name])
        c.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
        c.execute('CREATE INDEX value_idx ON records(value)')
        c.execute('INSERT INTO records(value) VALUES (?)', ('EGO_SYNTHETIC_' + name + '_SECRET',))
        c.commit()
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        c.close()
    return root, keys


def test_encrypted_reopen_wrong_key_and_separate_domains(databases):
    root, keys = databases
    c = open_database(root / 'main.db', keys['main'])
    assert c.execute('SELECT value FROM records').fetchone()[0] == 'EGO_SYNTHETIC_main_SECRET'
    c.close()
    for wrong in (keys['profile'], secrets.token_bytes(32)):
        with pytest.raises(StorageFailure, match='DATABASE_LOCKED_OR_INVALID'):
            open_database(root / 'main.db', wrong)


def test_plain_sqlite_binding_fails_closed(tmp_path):
    import sqlite3
    with pytest.raises(StorageFailure, match='SQLCIPHER_UNAVAILABLE'):
        open_database(tmp_path / 'never.db', b'x' * 32, driver=sqlite3)
    assert not (tmp_path / 'never.db').exists()


def test_active_wal_index_and_closed_database_have_no_plaintext(databases):
    root, keys = databases
    c = open_database(root / 'main.db', keys['main'])
    c.execute('INSERT INTO records(value) VALUES (?)', ('EGO_LIVE_WAL_SENTINEL',))
    c.commit()
    assert (root / 'main.db-wal').stat().st_size > 32
    for path in root.iterdir():
        if path.is_file():
            raw = path.read_bytes()
            assert b'EGO_SYNTHETIC' not in raw
            assert b'EGO_LIVE_WAL_SENTINEL' not in raw
    c.close()


def test_recovery_rejects_wrong_secret_and_tamper_without_output(databases, tmp_path):
    root, keys = databases
    secret = secrets.token_urlsafe(32)
    backup = snapshot(root, keys, secret)
    for altered, candidate in [(backup, secrets.token_urlsafe(32)),
                                (bytes([backup[0] ^ 1]) + backup[1:], secret),
                                (backup[:10] + bytes([backup[10] ^ 1]) + backup[11:], secret),
                                (backup[:-1] + bytes([backup[-1] ^ 1]), secret)]:
        destination = tmp_path / 'must-not-exist'
        with pytest.raises(BackupFailure):
            restore(altered, candidate, destination)
        assert not destination.exists()


def test_restore_validates_and_never_overwrites(databases, tmp_path):
    root, keys = databases
    secret = secrets.token_urlsafe(32)
    backup = snapshot(root, keys, secret)
    target = tmp_path / 'restored'
    recovered = restore(backup, secret, target)
    assert recovered == keys
    c = open_database(target / 'profile.db', recovered['profile'])
    assert c.execute('SELECT value FROM records').fetchone()[0] == 'EGO_SYNTHETIC_profile_SECRET'
    c.close()
    marker = target / 'keep'
    marker.write_text('existing data')
    with pytest.raises(BackupFailure, match='DESTINATION_EXISTS'):
        restore(backup, secret, target)
    assert marker.read_text() == 'existing data'


def test_restore_in_fresh_process_without_source_or_os_keys(databases, tmp_path):
    root, keys = databases
    secret = secrets.token_urlsafe(32)
    original = tmp_path / 'synthetic.backup'
    original.write_bytes(snapshot(root, keys, secret))
    original_bytes = original.read_bytes()
    target = tmp_path / 'fresh-process'
    # Child gets only backup path, new destination and secret via stdin. No source
    # path/DB keys/DPAPI wrappers, and starts in a separate empty cwd.
    cwd = tmp_path / 'clean'
    cwd.mkdir()
    code = '''
import json,sys
from pathlib import Path
from server.ego_core.vault.crypto_restore.backup import restore
from server.ego_core.vault.crypto_restore.storage import open_database
args=json.load(sys.stdin)
keys=restore(Path(args['backup']).read_bytes(),args['secret'],Path(args['target']))
c=open_database(Path(args['target'])/'main.db',keys['main'])
assert c.execute('SELECT value FROM records').fetchone()[0]=='EGO_SYNTHETIC_main_SECRET'
c.close()
print('RESTORE_VERIFIED')
'''
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    r = subprocess.run([sys.executable, '-c', code], input=json.dumps(
        {'backup': str(original), 'target': str(target), 'secret': secret}),
        capture_output=True, text=True, cwd=cwd, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'RESTORE_VERIFIED'
    assert original.read_bytes() == original_bytes


def test_non_windows_dpapi_has_no_fallback():
    if os.name == 'nt':
        pytest.skip('Non-Windows denial test')
    with pytest.raises(StorageFailure, match='WINDOWS_DPAPI_REQUIRED'):
        protect(b'synthetic')


@pytest.mark.skipif(os.name != 'nt', reason='BLOCKED: native Windows user-scope DPAPI')
def test_windows_dpapi_roundtrip_and_tamper():
    value = secrets.token_bytes(32)
    wrapped = protect(value)
    assert value not in wrapped
    assert unprotect(wrapped) == value
    with pytest.raises(StorageFailure):
        unprotect(wrapped[:-8] + b'badbytes')
