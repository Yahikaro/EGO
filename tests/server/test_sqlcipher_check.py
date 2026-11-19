"""Fail-closed capability checks use only disposable synthetic databases."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys

import pytest


def checker():
    assert importlib.util.find_spec('scripts.sqlcipher_check') is not None, 'SQLCipher capability checker is required'
    return importlib.import_module('scripts.sqlcipher_check')


def assert_safe_report(report):
    assert set(report) == {'status', 'reason', 'cipher_version'}
    assert report['status'] in {'PASS', 'BLOCKED', 'FAIL'}
    assert isinstance(report['reason'], str)
    assert report['cipher_version'] is None or isinstance(report['cipher_version'], str)


def test_installed_sqlcipher_really_encrypts_reopens_and_rejects_wrong_key(capfd):
    report = checker().check_sqlcipher()
    assert_safe_report(report)
    assert report['status'] == 'PASS', report
    assert report['reason'] == 'SQLCIPHER_VERIFIED'
    assert report['cipher_version']
    assert capfd.readouterr() == ('', '')


def test_stdlib_sqlite_is_not_an_encryption_fallback():
    report = checker().check_sqlcipher(driver=sqlite3)
    assert report == {'status': 'FAIL', 'reason': 'SQLCIPHER_NOT_ACTIVE', 'cipher_version': None}


def test_missing_binding_is_explicit_and_does_not_try_sqlite(monkeypatch):
    module = checker()
    original = importlib.import_module

    def missing(name, *args, **kwargs):
        if name == 'sqlcipher3.dbapi2':
            raise ModuleNotFoundError('private-path synthetic-secret', name='sqlcipher3')
        assert name != 'sqlite3', 'Plain SQLite must never be a fallback'
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, 'import_module', missing)
    assert module.check_sqlcipher() == {
        'status': 'BLOCKED', 'reason': 'SQLCIPHER_NOT_INSTALLED', 'cipher_version': None}


@pytest.mark.parametrize('error', [ImportError('DLL private-path synthetic-secret'),
                                 OSError('DLL private-path synthetic-secret'),
                                 RuntimeError('loader private-path synthetic-secret'),
                                 ModuleNotFoundError('private-path synthetic-secret', name='_dependency')])
def test_broken_native_binding_has_safe_structured_error(monkeypatch, error):
    module = checker()

    def broken(name):
        raise error

    monkeypatch.setattr(importlib, 'import_module', broken)
    report = module.check_sqlcipher()
    assert report == {'status': 'FAIL', 'reason': 'SQLCIPHER_LOAD_FAILED', 'cipher_version': None}


def test_module_can_load_without_importing_sqlcipher(monkeypatch):
    module = checker()
    original = __import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith('sqlcipher3'), 'Binding import must be deferred until checking'
        return original(name, *args, **kwargs)

    monkeypatch.setattr('builtins.__import__', guarded)
    runpy.run_path(module.__file__, run_name='capability_import_only')


class ConnectionProxy:
    def __init__(self, connection, driver):
        self.connection = connection
        self.driver = driver

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, *args):
        if sql == 'PRAGMA cipher_version' and self.driver.fake_version:
            return self.connection.execute("SELECT '4.12.0 community'")
        if sql.startswith('PRAGMA key') and self.driver.ignore_wrong_key:
            if self.driver.first_key is None:
                self.driver.first_key = sql
            sql = self.driver.first_key
        return self.connection.execute(sql, *args)


class DriverProxy:
    """Deliberately weaken real backends so checks must detect actual failure."""
    def __init__(self, backend, *, fake_version=False, ignore_wrong_key=False):
        self.backend = backend
        self.DatabaseError = backend.DatabaseError
        self.fake_version = fake_version
        self.ignore_wrong_key = ignore_wrong_key
        self.first_key = None
        self.paths = []

    def connect(self, path):
        self.paths.append(Path(path))
        if os.name != 'nt':
            assert Path(path).parent.stat().st_mode & 0o777 == 0o700
        return ConnectionProxy(self.backend.connect(path), self)


def test_claimed_cipher_version_cannot_hide_a_plaintext_database():
    driver = DriverProxy(sqlite3, fake_version=True)
    report = checker().check_sqlcipher(driver=driver)
    assert report['status'] == 'FAIL'
    assert report['reason'] == 'SQLCIPHER_PLAINTEXT_DATABASE'
    assert all(not path.parent.exists() for path in driver.paths)


def test_wrong_key_is_actually_tried_and_must_be_rejected():
    real_driver = importlib.import_module('sqlcipher3.dbapi2')
    driver = DriverProxy(real_driver, ignore_wrong_key=True)
    report = checker().check_sqlcipher(driver=driver)
    assert report['status'] == 'FAIL'
    assert report['reason'] == 'SQLCIPHER_WRONG_KEY_ACCEPTED'
    assert len(driver.paths) == 3, 'Create, reopen correctly, then reopen with a different key'
    assert all(not path.parent.exists() for path in driver.paths)


def test_driver_failure_does_not_disclose_exception_data():
    class BrokenDriver:
        def connect(self, path):
            raise RuntimeError('private-path synthetic-secret')

    report = checker().check_sqlcipher(driver=BrokenDriver())
    assert report == {'status': 'FAIL', 'reason': 'SQLCIPHER_CHECK_FAILED', 'cipher_version': None}


def test_cli_emits_safe_json_and_success_only_after_actual_pass():
    module = checker()
    result = subprocess.run([sys.executable, module.__file__], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert_safe_report(report)
    assert report['status'] == 'PASS'
    assert result.stderr == ''


def test_cli_missing_binding_returns_nonzero_without_traceback():
    module = checker()
    code = '''
import importlib, runpy, sys
original = importlib.import_module
def missing(name, *args, **kwargs):
    if name == 'sqlcipher3.dbapi2':
        raise ModuleNotFoundError('private-path synthetic-secret', name='sqlcipher3')
    return original(name, *args, **kwargs)
importlib.import_module = missing
runpy.run_path(sys.argv[1], run_name='__main__')
'''
    result = subprocess.run([sys.executable, '-c', code, module.__file__],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert json.loads(result.stdout) == {
        'status': 'BLOCKED', 'reason': 'SQLCIPHER_NOT_INSTALLED', 'cipher_version': None}
    assert result.stderr == ''
