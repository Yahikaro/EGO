"""Fail-closed SQLCipher capability probe using disposable synthetic data only.

The binding is deliberately imported inside check_sqlcipher, so diagnostic tools
can report missing or incompatible native bindings without failing to import.
This module never opens an application database or falls back to sqlite3.
"""
from contextlib import closing
import importlib
import json
from pathlib import Path
import re
import secrets
import tempfile


def check_sqlcipher(*, driver=None) -> dict:
    """Verify the active binding, encrypted disk roundtrip and wrong-key denial.

    ``driver`` is a test seam; normal callers must leave it unset. Results contain
    fixed reason codes and a validated engine version, never exception details,
    keys, temporary paths or database contents.
    """
    def report(status, reason, version=None):
        return {'status': status, 'reason': reason, 'cipher_version': version}

    if driver is None:
        try:
            driver = importlib.import_module('sqlcipher3.dbapi2')
        except ModuleNotFoundError as error:
            if error.name in ('sqlcipher3', 'sqlcipher3.dbapi2'):
                return report('BLOCKED', 'SQLCIPHER_NOT_INSTALLED')
            return report('FAIL', 'SQLCIPHER_LOAD_FAILED')
        except Exception:
            return report('FAIL', 'SQLCIPHER_LOAD_FAILED')

    version = None
    try:
        # TemporaryDirectory uses a private directory (0700 on POSIX); Windows
        # inherits the current user's temporary-directory ACL. No app data lives
        # here. Cleanup occurs after every success and failure path.
        with tempfile.TemporaryDirectory(prefix='ego-sqlcipher-check-') as directory:
            path = Path(directory) / 'synthetic.db'
            key = secrets.token_hex(32)
            wrong_key = ('00' if key[:2] != '00' else '01') + key[2:]
            sentinel = 'EGO_SYNTHETIC_SQLCIPHER_CHECK'
            with closing(driver.connect(str(path))) as connection:
                connection.execute('PRAGMA cipher_log_level = NONE')
                row = connection.execute('PRAGMA cipher_version').fetchone()
                if not row or not isinstance(row[0], str) or not re.fullmatch(
                        r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(?: (?:community|enterprise))?', row[0]):
                    return report('FAIL', 'SQLCIPHER_NOT_ACTIVE')
                version = row[0]
                connection.execute('PRAGMA key = "x\'' + key + '\'"')
                connection.execute('CREATE TABLE probe(value TEXT NOT NULL)')
                connection.execute('INSERT INTO probe(value) VALUES (?)', (sentinel,))
                connection.commit()

            raw = path.read_bytes()
            if not raw or raw.startswith(b'SQLite format 3\x00') or sentinel.encode() in raw:
                return report('FAIL', 'SQLCIPHER_PLAINTEXT_DATABASE', version)

            with closing(driver.connect(str(path))) as connection:
                connection.execute('PRAGMA cipher_log_level = NONE')
                connection.execute('PRAGMA key = "x\'' + key + '\'"')
                if connection.execute('SELECT value FROM probe').fetchall() != [(sentinel,)]:
                    return report('FAIL', 'SQLCIPHER_ROUNDTRIP_FAILED', version)

            with closing(driver.connect(str(path))) as connection:
                connection.execute('PRAGMA cipher_log_level = NONE')
                connection.execute('PRAGMA key = "x\'' + wrong_key + '\'"')
                try:
                    # Applying a key does not validate it. Force a disk/schema
                    # read on a fresh connection and require database rejection.
                    connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
                except driver.DatabaseError:
                    pass
                else:
                    return report('FAIL', 'SQLCIPHER_WRONG_KEY_ACCEPTED', version)
        return report('PASS', 'SQLCIPHER_VERIFIED', version)
    except Exception:
        # Native loader/runtime errors can contain local paths or input data.
        # Diagnostics intentionally expose only this fixed reason code.
        return report('FAIL', 'SQLCIPHER_CHECK_FAILED', version)


if __name__ == '__main__':
    result = check_sqlcipher()
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
