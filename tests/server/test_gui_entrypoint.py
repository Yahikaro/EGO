"""Real Qt entrypoint lease, event-loop readiness and safe failure tests."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import socket
import errno
import sys
import tempfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


def entry():
    try:
        return importlib.import_module('windows_ui.__main__')
    except ModuleNotFoundError:
        pytest.fail('Native Qt GUI entrypoint and PySide6 are required')


def test_instance_identifier_is_project_specific_and_canonical(tmp_path):
    m = entry()
    assert m.instance_identifier(tmp_path) == m.instance_identifier(tmp_path / '.')
    assert m.instance_identifier(tmp_path) != m.instance_identifier(tmp_path / 'other')
    assert '/' not in m.instance_identifier(tmp_path)


def test_handoff_rejects_arbitrary_existing_files(tmp_path):
    m = entry()
    arbitrary = tmp_path / 'do-not-overwrite.txt'
    arbitrary.write_text('keep')
    with pytest.raises(ValueError):
        m.load_handoff(arbitrary, None)
    assert arbitrary.read_text() == 'keep'


def handoff(directory):
    path = Path(directory)
    (path / 'request.json').write_text(json.dumps({'token': 'synthetic-private-token'}))
    return path / 'ready.json', path / 'error.json'


def _isolate(root):
    """The real window auto-starts Core. Give this throwaway installation free
    ports so it can never occupy the user's 18443/18081."""
    from runtime.adapters.ports import choose_loopback_port
    from server.ego_core.config import CoreConfig, save_config
    data = Path(root) / 'data'
    data.mkdir(exist_ok=True)
    if not (data / 'config.json').exists():
        api = choose_loopback_port()
        runtime = choose_loopback_port()
        while runtime == api:
            runtime = choose_loopback_port()
        save_config(data / 'config.json', CoreConfig(api_port=api, runtime_port=runtime))


@pytest.fixture(autouse=True)
def _stop_test_core(tmp_path):
    yield
    # A window that reached its event loop may have started this installation's
    # Core, possibly still claiming its lease. Stop it until none is left.
    if not (tmp_path / 'data').is_dir():
        return
    import psutil
    from server.ego_core import control
    from scripts.force_close import process_matches_core

    def owned():
        found = []
        for proc in psutil.process_iter(['cmdline']):
            try:
                if process_matches_core(proc.info['cmdline'] or [], tmp_path):
                    found.append(proc)
            except (psutil.Error, OSError):
                continue
        return found
    deadline = time.monotonic() + 20
    while owned() and time.monotonic() < deadline:
        try:
            control.stop(tmp_path)
        except Exception:
            pass
        time.sleep(.3)
    for proc in owned():  # only this test's own throwaway Core
        proc.kill()


def launch(root, ready, error, *, source=None):
    _isolate(root)
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    command = [sys.executable, '-m', 'windows_ui', '--root', str(root),
               '--ready-file', str(ready), '--error-file', str(error)]
    if source:
        command = [sys.executable, '-c', source, *command[3:]]
    return subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def wait_record(process, ready, error):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if ready.exists():
            return json.loads(ready.read_text())
        if error.exists():
            pytest.fail(f'GUI startup failed: {error.read_text()}')
        if process.poll() is not None:
            out, err = process.communicate()
            pytest.fail(f'GUI exited {process.returncode}: {out!r} {err!r}')
        time.sleep(.03)
    pytest.fail('GUI did not reach its event loop')


def test_real_window_reaches_loop_and_second_instance_activates_only(tmp_path):
    # Check Qt first: a missing GUI dependency is a failure, never a skip.
    entry()
    importlib.import_module('PySide6.QtWidgets')
    if os.name != 'nt':
        try:
            probe = socket.socket(socket.AF_UNIX)
            probe.close()
        except OSError as exc:
            if exc.errno == errno.EPERM:
                pytest.skip('BLOCKED: Linux sandbox denies AF_UNIX; native Windows named-pipe activation pending')
            raise
    # No fake window: readiness requires the actual ControlCenterWindow to show.
    with tempfile.TemporaryDirectory(prefix='ego-gui-bootstrap-') as first, \
         tempfile.TemporaryDirectory(prefix='ego-gui-bootstrap-') as second:
        ready1, error1 = handoff(first)
        ready2, error2 = handoff(second)
        process1 = launch(tmp_path, ready1, error1)
        process2 = None
        try:
            record1 = wait_record(process1, ready1, error1)
            assert process1.pid in (record1['pid'], record1['parent_pid'])  # venv launcher on Windows
            assert record1['status'] == 'ready'
            assert record1['mode'] == 'window'
            process2 = launch(tmp_path, ready2, error2)
            record2 = wait_record(process2, ready2, error2)
            assert process2.pid in (record2['pid'], record2['parent_pid'])
            assert record2['mode'] == 'activated'
            assert process2.wait(timeout=5) == 0
            assert process1.poll() is None
        finally:
            if process2 and process2.poll() is None:
                process2.terminate()
                process2.communicate(timeout=5)
            elif process2:
                process2.communicate(timeout=5)
            process1.terminate()
            process1.communicate(timeout=5)


def test_window_construction_failure_is_fixed_code_and_never_ready(tmp_path):
    source = '''
import sys
import windows_ui.window
class BrokenWindow:
    def __init__(self, *args, **kwargs):
        raise RuntimeError('api-key-private-path-secret')
windows_ui.window.ControlCenterWindow = BrokenWindow
from windows_ui.__main__ import main
raise SystemExit(main(sys.argv[1:]))
'''
    with tempfile.TemporaryDirectory(prefix='ego-gui-bootstrap-') as directory:
        ready, error = handoff(directory)
        process = launch(tmp_path, ready, error, source=source)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 31
        assert not ready.exists()
        record = json.loads(error.read_text())
        assert record['reason'] == 'GUI_STARTUP_FAILED'
        assert 'secret' not in error.read_text() + out.decode() + err.decode()


def test_handoff_record_never_overwrites_a_preexisting_file():
    m = entry()
    with tempfile.TemporaryDirectory(prefix='ego-gui-bootstrap-') as directory:
        ready, error = handoff(directory)
        trusted = m.load_handoff(ready, error)
        ready.write_text('keep')
        with pytest.raises(FileExistsError):
            m.write_record(trusted)
        assert ready.read_text() == 'keep'


def test_entry_reports_ready_after_real_window_show_and_event_loop(tmp_path):
    # The sandbox denies AF_UNIX. Inject only the IPC endpoint; use the actual
    # window, lifetime lock, event loop, and private bootstrap handoff.
    source = '''
import sys
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication
import PySide6.QtNetwork
class Endpoint(QObject):
    newConnection = Signal()
    UserAccessOption = 1
    @staticmethod
    def removeServer(name): return True
    def setSocketOptions(self, options): pass
    def listen(self, name): return True
    def close(self): pass
app = QApplication([])
PySide6.QtNetwork.QLocalServer = Endpoint
from windows_ui.__main__ import main
raise SystemExit(main(sys.argv[1:]))
'''
    with tempfile.TemporaryDirectory(prefix='ego-gui-bootstrap-') as directory:
        ready, error = handoff(directory)
        process = launch(tmp_path, ready, error, source=source)
        try:
            record = wait_record(process, ready, error)
            assert record['mode'] == 'window'
            assert process.pid in (record['pid'], record['parent_pid'])
            assert process.poll() is None
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)


def test_settings_location_rejects_symlink_redirection(tmp_path, monkeypatch):
    m = entry()
    from PySide6.QtCore import QStandardPaths
    external = tmp_path/'external'
    external.mkdir()
    base = tmp_path/'base'
    base.mkdir()
    try:
        (base/'EGO').symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('Windows symlink creation permission unavailable')
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda kind: str(base))
    with pytest.raises(ValueError):
        m.settings_directory()
    assert list(external.iterdir()) == []


def test_live_lease_routes_second_instance_to_activation_without_window(tmp_path):
    m = entry()
    from PySide6.QtCore import QLockFile
    lease = QLockFile(str(m.settings_directory()/(m.instance_identifier(tmp_path)+'.lock')))
    lease.setStaleLockTime(0)
    assert lease.tryLock(0)
    source = '''
import sys
import windows_ui.__main__ as entry
entry.activate_existing = lambda name: True
raise SystemExit(entry.main(sys.argv[1:]))
'''
    try:
        with tempfile.TemporaryDirectory(prefix='ego-gui-bootstrap-') as directory:
            ready, error = handoff(directory)
            with launch(tmp_path, ready, error, source=source) as process:
                record = wait_record(process, ready, error)
                assert record['mode'] == 'activated'
                process.communicate(timeout=5)
                assert process.returncode == 0
    finally:
        lease.unlock()


def test_unsupported_secret_cli_argument_has_fixed_safe_error():
    result = subprocess.run([sys.executable, '-m', 'windows_ui', '--api-key', 'private-secret-token'],
                            cwd=ROOT, capture_output=True, timeout=5)
    assert result.returncode == 30
    assert b'private-secret-token' not in result.stdout + result.stderr
    assert b'INVALID_BOOTSTRAP_HANDOFF' in result.stderr
