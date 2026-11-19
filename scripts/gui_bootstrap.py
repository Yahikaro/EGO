"""Standard-library Windows launcher. Only fixed diagnostic codes are recorded.

The console survives installation and readiness checks. The independent pythonw
child owns the GUI; closing this launcher never stops an existing Core process.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import tempfile
import time
import venv

ROOT = Path(__file__).resolve().parents[1]
CODES = {
    'UNSUPPORTED_PYTHON': 10, 'WINDOWS_REQUIRED': 11, 'VENV_FAILED': 12,
    'DEPENDENCY_INSTALL_FAILED': 13, 'SQLCIPHER_NAMESPACE_CONFLICT': 14,
    'DEPENDENCY_PROBE_FAILED': 15, 'QT_IMPORT_FAILED': 16,
    'SQLCIPHER_CHECK_FAILED': 17, 'GUI_EXITED_BEFORE_READY': 18,
    'GUI_READY_TIMEOUT': 19, 'BOOTSTRAP_FAILED': 20, 'GUI_LAUNCH_FAILED': 21,
    'UNSAFE_PROJECT_PATH': 22, 'INVALID_BOOTSTRAP_HANDOFF': 30,
    'GUI_READY_UNCONFIRMED': 23, 'GUI_STARTUP_FAILED': 31, 'GUI_INSTANCE_ACTIVATION_FAILED': 32,
    'GUI_INSTANCE_LISTEN_FAILED': 33, 'GUI_READY_WRITE_FAILED': 34,
}


def evaluate_dependencies(versions: dict, checks: dict) -> str:
    if versions.get('sqlcipher3-binary'):
        return 'SQLCIPHER_NAMESPACE_CONFLICT'
    if versions.get('PySide6') != '6.10.3' or versions.get('sqlcipher3') != '0.6.4':
        return 'MISSING_DEPENDENCIES'
    if not checks.get('qt'):
        return 'QT_IMPORT_FAILED'
    if checks.get('sqlcipher') != 'PASS':
        return 'SQLCIPHER_CHECK_FAILED'
    return 'READY'


def _owned(record, pid: int) -> bool:
    """The record belongs to the started process or to the interpreter a
    Windows venv launcher (Scripts/pythonw.exe) runs as its child. The
    private token still proves the record came from this launch."""
    return pid in (record.get('pid'), record.get('parent_pid'))


def read_ready(path: Path, *, pid: int, token: str) -> bool:
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return False
        record = json.loads(path.read_text(encoding='utf-8'))
        return (_owned(record, pid) and record.get('token') == token
                and record.get('status') == 'ready')
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def read_error(path: Path, *, pid: int, token: str) -> str | None:
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return None
        record = json.loads(path.read_text(encoding='utf-8'))
        reason = record.get('reason')
        if (_owned(record, pid) and record.get('token') == token
                and record.get('status') == 'error' and reason in CODES
                and record.get('code') == CODES[reason]):
            return reason
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


PROBE_CACHE = 'ego-dependency-probe.json'


def dependency_stamp(environment: Path) -> str | None:
    """Fingerprint of installed packages and requirement files. Any pip install,
    upgrade or removal changes site-packages' modification time."""
    try:
        parts = [str((environment / 'Lib' / 'site-packages').stat().st_mtime_ns),
                 str((environment / 'pyvenv.cfg').stat().st_mtime_ns)]
        for name in ('requirements.txt', 'requirements-windows.txt', 'requirements-gui.txt'):
            parts.append(hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
        return hashlib.sha256('|'.join(parts).encode()).hexdigest()
    except OSError:
        return None


def read_probe_cache(environment: Path) -> str | None:
    try:
        path = environment / PROBE_CACHE
        if path.is_symlink() or path.stat().st_size > 1024:
            return None
        return json.loads(path.read_text(encoding='utf-8')).get('stamp')
    except (OSError, ValueError, AttributeError):
        return None


def write_probe_cache(environment: Path, stamp: str | None) -> None:
    if stamp is None:
        return
    try:
        (environment / PROBE_CACHE).write_text(json.dumps({'stamp': stamp, 'status': 'READY'}), encoding='utf-8')
    except OSError:
        pass  # The cache only saves time; a failed write just re-probes next launch.


def validate_project_python(python: Path) -> bool:
    code = "import sys,struct;sys.exit(0 if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 1)"
    try:
        result, _ = run_quiet([str(python), '-c', code], timeout=15)
        return result == 0
    except OSError:
        return False


def wait_for_ready(process, path: Path, *, token: str, timeout: float = 30) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if read_ready(path, pid=process.pid, token=token):
            exit_code = process.poll()
            if exit_code is None:
                return 'READY'
            # A second instance exits normally after receiving activation ACK.
            try:
                if exit_code == 0 and json.loads(path.read_text(encoding='utf-8')).get('mode') == 'activated':
                    return 'READY'
            except (OSError, ValueError, AttributeError):
                pass
            return 'GUI_EXITED_BEFORE_READY'
        if process.poll() is not None:
            return 'GUI_EXITED_BEFORE_READY'
        time.sleep(.05)
    return 'GUI_READY_TIMEOUT'


def safe_log(reason: str, root: Path = ROOT) -> None:
    """No paths, environment, pip output, exceptions, or user values in logs."""
    if reason not in CODES and reason != 'GUI_READY':
        reason = 'BOOTSTRAP_FAILED'
    try:
        directory = root / 'logs'
        if directory.is_symlink():
            return
        directory.mkdir(exist_ok=True)
        path = directory / 'gui-bootstrap.log'
        if path.is_symlink():
            return
        if path.exists() and path.stat().st_size > 262144:
            backup=directory/'gui-bootstrap.log.1'
            if backup.is_symlink(): return
            os.replace(path,backup)
        with path.open('a', encoding='ascii') as stream:
            stream.write(json.dumps({'status': 'PASS' if reason == 'GUI_READY' else 'WARN' if reason == 'GUI_READY_UNCONFIRMED' else 'FAIL', 'code': reason,
                'timestamp': datetime.now(timezone.utc).isoformat()}) + '\n')
    except OSError:
        pass


def stage(message: str) -> None:
    print(f'[EGO] {message}', flush=True)


def fail(reason: str) -> int:
    safe_log(reason)
    stage(f'FAIL {reason}. See logs/gui-bootstrap.log. Keep this window open for diagnostics.')
    return CODES.get(reason, CODES['BOOTSTRAP_FAILED'])


def run_quiet(command: list[str], *, timeout: float, progress: bool = False) -> tuple[int, str]:
    """Discard all installer output: index URLs may carry credentials."""
    output = subprocess.DEVNULL if progress else subprocess.PIPE
    with subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.DEVNULL,
                          text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as child:
        deadline = time.monotonic() + timeout
        while True:
            try:
                stdout, _ = child.communicate(timeout=min(10, max(.1, deadline - time.monotonic())))
                return child.returncode, stdout or ''
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    child.kill()
                    child.communicate()
                    return 124, ''
                if progress:
                    stage('Installing binary dependencies; still working...')


def dependency_probe(python: Path) -> str:
    code = '''
import importlib.metadata as m, json
versions = {}
for name in ('PySide6', 'sqlcipher3', 'sqlcipher3-binary'):
    try: versions[name] = m.version(name)
    except m.PackageNotFoundError: pass
checks = {'qt': False, 'sqlcipher': 'FAIL'}
try:
    from PySide6 import QtCore, QtWidgets, QtNetwork
    checks['qt'] = True
except Exception: pass
try:
    from scripts.sqlcipher_check import check_sqlcipher
    checks['sqlcipher'] = check_sqlcipher()['status']
except Exception: pass
print(json.dumps({'versions': versions, 'checks': checks}))
'''
    returncode, output = run_quiet([str(python), '-c', code], timeout=45)
    if returncode:
        return 'DEPENDENCY_PROBE_FAILED'
    try:
        record = json.loads(output)
        result = evaluate_dependencies(record['versions'], record['checks'])
        if result == 'READY':
            # Check every pinned dependency, including nested requirements.txt.
            code = '''
import importlib.metadata as m, pathlib, sys
root = pathlib.Path(sys.argv[1])
def check(path):
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        if line.startswith('-r '):
            if not check(path.parent / line[3:].strip()): return False
        else:
            name, expected = line.split('==', 1)
            try:
                if m.version(name) != expected: return False
            except m.PackageNotFoundError: return False
    return True
sys.exit(0 if all(check(root / x) for x in ('requirements-windows.txt', 'requirements-gui.txt')) else 1)
'''
            resultcode, _ = run_quiet([str(python), '-c', code, str(ROOT)], timeout=30)
            if resultcode:
                return 'MISSING_DEPENDENCIES'
        return result
    except (ValueError, KeyError, TypeError):
        return 'DEPENDENCY_PROBE_FAILED'


def main() -> int:
    if sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
        return fail('UNSUPPORTED_PYTHON')
    if os.name != 'nt':
        return fail('WINDOWS_REQUIRED')
    try:
        stage('Checking project virtual environment (Python 3.12 x64).')
        environment = ROOT / '.venv'
        if environment.is_symlink():
            return fail('UNSAFE_PROJECT_PATH')
        python = environment / 'Scripts' / 'python.exe'
        if not python.exists():
            stage('Creating project virtual environment. No administrator rights required.')
            try:
                venv.EnvBuilder(with_pip=True).create(environment)
            except Exception:
                return fail('VENV_FAILED')
        if not validate_project_python(python):
            return fail('UNSUPPORTED_PYTHON')
        stamp = dependency_stamp(environment)
        if stamp is not None and read_probe_cache(environment) == stamp:
            # Same .venv packages and requirements as the last verified probe.
            status = 'READY'
        else:
            stage('Checking GUI imports and actual SQLCipher encryption.')
            status = dependency_probe(python)
        if status == 'MISSING_DEPENDENCIES':
            stage('Installing required binary wheels. Model files are not downloaded.')
            result, _ = run_quiet([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                                   '--no-input', '--only-binary=:all:', '-r', str(ROOT/'requirements-windows.txt'),
                                   '-r', str(ROOT/'requirements-gui.txt')], timeout=1200, progress=True)
            if result:
                return fail('DEPENDENCY_INSTALL_FAILED')
            status = dependency_probe(python)
        if status != 'READY':
            return fail(status if status in CODES else 'DEPENDENCY_PROBE_FAILED')
        write_probe_cache(environment, dependency_stamp(environment))
        pythonw = environment / 'Scripts' / 'pythonw.exe'
        if not pythonw.is_file():
            return fail('GUI_LAUNCH_FAILED')
        stage('Starting Control Center; waiting for the native window event loop.')
        with tempfile.TemporaryDirectory(prefix='ego-gui-bootstrap-') as directory:
            private = Path(directory)
            token = secrets.token_hex(32)
            with (private/'request.json').open('x', encoding='utf-8') as stream:
                json.dump({'token': token}, stream)
            try:
                process = subprocess.Popen([str(pythonw), '-m', 'windows_ui', '--root', str(ROOT),
                                            '--ready-file', str(private/'ready.json'),
                                            '--error-file', str(private/'error.json')],
                                           cwd=ROOT, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError:
                return fail('GUI_LAUNCH_FAILED')
            result = wait_for_ready(process, private/'ready.json', token=token, timeout=90)
            if result == 'GUI_READY_TIMEOUT' and process.poll() is None:
                # The event-loop handoff is not proof that the GUI is dead.
                # In M1.3 the old launcher terminated a healthy window here.
                # Never destroy the GUI or a Core just because an ACK is late.
                safe_log('GUI_READY_UNCONFIRMED')
                stage('GUI readiness was not confirmed within 90 seconds. The live window was preserved.')
                stage('If EGO is visible, use it normally. If not, inspect logs/gui-bootstrap.log.')
                return 0
            if result != 'READY':
                reason = read_error(private/'error.json', pid=process.pid, token=token)
                return fail(reason or result)
        safe_log('GUI_READY')
        stage('Control Center ready.')
        return 0
    except Exception:
        return fail('BOOTSTRAP_FAILED')


if __name__ == '__main__':
    raise SystemExit(main())
