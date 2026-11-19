"""Bootstrap handoff and fail-closed dependency checks."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def module():
    try:
        return importlib.import_module('scripts.gui_bootstrap')
    except ModuleNotFoundError:
        pytest.fail('The stdlib GUI bootstrap is required')


def test_dependency_probe_rejects_conflicting_sqlcipher_namespace():
    result = module().evaluate_dependencies({'sqlcipher3-binary': '0.5.4'}, {})
    assert result == 'SQLCIPHER_NAMESPACE_CONFLICT'


def test_dependency_probe_requires_actual_qt_import_and_sqlcipher_check():
    m = module()
    versions = {'PySide6': '6.10.3', 'sqlcipher3': '0.6.4'}
    assert m.evaluate_dependencies(versions, {'qt': False, 'sqlcipher': 'PASS'}) == 'QT_IMPORT_FAILED'
    assert m.evaluate_dependencies(versions, {'qt': True, 'sqlcipher': 'FAIL'}) == 'SQLCIPHER_CHECK_FAILED'
    assert m.evaluate_dependencies(versions, {'qt': True, 'sqlcipher': 'PASS'}) == 'READY'


def test_ready_handoff_requires_matching_child_pid_and_private_token(tmp_path):
    m = module()
    path = tmp_path / 'ready.json'
    path.write_text(json.dumps({'pid': 42, 'token': 'secret', 'status': 'ready'}))
    assert m.read_ready(path, pid=42, token='secret')
    assert not m.read_ready(path, pid=43, token='secret')
    assert not m.read_ready(path, pid=42, token='other')
    path.write_text('not json')
    assert not m.read_ready(path, pid=42, token='secret')


def test_ready_handoff_accepts_interpreter_started_by_windows_venv_launcher(tmp_path):
    # .venv\Scripts\pythonw.exe runs the real interpreter as its child: the
    # bootstrap knows the launcher PID (7), the GUI reports its own (8).
    m = module()
    path = tmp_path / 'ready.json'
    path.write_text(json.dumps({'pid': 8, 'parent_pid': 7, 'token': 'secret', 'status': 'ready'}))
    assert m.read_ready(path, pid=7, token='secret')
    assert not m.read_ready(path, pid=9, token='secret')
    assert not m.read_ready(path, pid=7, token='other')


def test_child_exit_without_ready_is_failure(tmp_path):
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    result = module().wait_for_ready(child, tmp_path/'missing.json', token='secret', timeout=2)
    assert result == 'GUI_EXITED_BEFORE_READY'


def test_existing_ready_file_is_not_accepted_from_other_process(tmp_path):
    path = tmp_path / 'ready.json'
    path.write_text(json.dumps({'pid': os.getpid(), 'token': 'old', 'status': 'ready'}))
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.1)'])
    assert module().wait_for_ready(child, path, token='new', timeout=2) == 'GUI_EXITED_BEFORE_READY'


def test_bat_is_ascii_crlf_quoted_and_waits_for_bootstrap():
    content = (Path(__file__).resolve().parents[2] / 'START_EGO_SERVER.bat').read_bytes()
    content.decode('ascii')
    assert b'\r\n' in content and b'\n' not in content.replace(b'\r\n', b'')
    text = content.decode('ascii')
    assert 'DisableDelayedExpansion' in text
    assert '"%~dp0scripts\\gui_bootstrap.py"' in text
    assert 'if errorlevel 1' in text.lower()
    assert 'pause' in text.lower()


def test_logs_contain_only_safe_structured_codes(tmp_path):
    m = module()
    m.safe_log('QT_IMPORT_FAILED', tmp_path)
    m.safe_log('api-key-private-path-secret', tmp_path)
    records = [json.loads(line) for line in (tmp_path/'logs'/'gui-bootstrap.log').read_text().splitlines()]
    from datetime import datetime
    assert [{k:v for k,v in row.items() if k!='timestamp'} for row in records] == [
        {'status': 'FAIL', 'code': 'QT_IMPORT_FAILED'}, {'status': 'FAIL', 'code': 'BOOTSTRAP_FAILED'}]
    assert all(datetime.fromisoformat(row['timestamp']).tzinfo is not None for row in records)
    assert all(set(row)=={'status','code','timestamp'} for row in records)


def test_timeout_does_not_report_pass(tmp_path):
    with subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(2)']) as child:
        try:
            assert module().wait_for_ready(child, tmp_path/'ready.json', token='token', timeout=.03) == 'GUI_READY_TIMEOUT'
        finally:
            child.terminate()
            child.wait(timeout=3)


def test_project_python_is_verified_before_using_existing_environment():
    assert module().validate_project_python(Path(sys.executable))
    assert not module().validate_project_python(Path('/definitely-missing-ego-python'))


def test_child_error_record_is_used_only_with_matching_handoff(tmp_path):
    m = module()
    path = tmp_path/'error.json'
    path.write_text(json.dumps({'pid': 42, 'token': 'token', 'status': 'error',
                                'reason': 'GUI_STARTUP_FAILED', 'code': 31}))
    assert m.read_error(path, pid=42, token='token') == 'GUI_STARTUP_FAILED'
    assert m.read_error(path, pid=43, token='token') is None
    path.write_text(json.dumps({'pid': 42, 'token': 'token', 'status': 'error',
                                'reason': 'api-key-secret', 'code': 31}))
    assert m.read_error(path, pid=42, token='token') is None


def test_ready_record_from_a_crashed_window_is_not_success(tmp_path):
    ready = tmp_path/'ready.json'
    source = '''
import json, os, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid': os.getpid(), 'token': 'token', 'status': 'ready', 'mode': 'window'}))
sys.exit(31)
'''
    with subprocess.Popen([sys.executable, '-c', source, str(ready)]) as child:
        child.wait(timeout=3)
        assert module().wait_for_ready(child, ready, token='token') == 'GUI_EXITED_BEFORE_READY'


def test_unconfirmed_readiness_is_a_warning_not_success_or_crash(tmp_path):
    m=module()
    m.safe_log('GUI_READY_UNCONFIRMED', tmp_path)
    record=json.loads((tmp_path/'logs'/'gui-bootstrap.log').read_text().splitlines()[-1])
    assert record['status']=='WARN'
    assert record['code']=='GUI_READY_UNCONFIRMED'


def test_launcher_does_not_kill_window_on_ready_timeout():
    source=(Path(__file__).resolve().parents[2]/'scripts/gui_bootstrap.py').read_text()
    assert "if result == 'GUI_READY_TIMEOUT' and process.poll() is None:" in source
    assert 'process.terminate()' not in source
    assert 'process.kill()' not in source
