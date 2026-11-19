"""Portable control-dispatch checks; native M1 Windows acceptance remains pending."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'tools/windows/runner.py'
CORE_BATS = {
    '10_CORE_SETUP': 'setup',
    '11_START_SERVER': 'start',
    '12_SERVER_STATUS': 'status',
    '13_STOP_SERVER': 'stop',
    '14_CORE_SMOKE_TEST': 'smoke',
}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'EGO space Кириллица ! &'
    (root / 'tools/windows').mkdir(parents=True)
    shutil.copy2(SOURCE, root / 'tools/windows/runner.py')
    (root / 'server/ego_core').mkdir(parents=True)
    (root / 'server/__init__.py').touch()
    (root / 'server/ego_core/__init__.py').touch()
    # A real subprocess stub proves module resolution, argv and inherited streams.
    (root / 'server/ego_core/control.py').write_text(
        'import json,sys\nfrom pathlib import Path\n'
        'root=Path.cwd()\naction=sys.argv[1]\n'
        "answer=input('Safe configuration prompt: ') if action in {'setup','configure'} else None\n"
        "(root/'control-invocation.json').write_text(json.dumps({'argv':sys.argv[1:],'cwd':str(root),'answer':answer}))\n"
        "print('CONTROL_STUB_' + action, flush=True)\n"
        "raise SystemExit(17 if action=='stop' else 0)\n", encoding='utf-8')
    return root


def invoke(project, *args, answer=''):
    return subprocess.run([sys.executable, str(project / 'tools/windows/runner.py'), *args],
                          cwd=project.parent, input=answer, capture_output=True, text=True, timeout=40)


@pytest.mark.parametrize('action', ['setup', 'start', 'status', 'stop', 'smoke', 'configure'])
def test_core_dispatch_uses_project_root_inherited_streams_and_exact_exit(project, action):
    result = invoke(project, 'core', '--action', action, answer='local configuration\n')
    expected_code = 17 if action == 'stop' else 0
    assert result.returncode == expected_code, result.stdout + result.stderr
    payload = json.loads((project / 'control-invocation.json').read_text())
    assert payload['argv'] == [action, '--root', str(project)]
    assert payload['cwd'] == str(project)
    assert payload['answer'] == ('local configuration' if action in {'setup', 'configure'} else None)
    assert 'CONTROL_STUB_' + action in result.stdout
    logs = list((project / 'logs').glob('*-core.log'))
    assert len(logs) == 1
    report = logs[0].read_text()
    assert 'Stage: CORE_CONTROL' in report
    assert 'Action: ' + action in report
    assert 'Exit code: ' + str(expected_code) in report
    assert 'Status: ' + ('FAIL' if expected_code else 'PASS') in report
    assert 'M1 native Windows acceptance: PENDING' in report
    assert 'CONTROL_STUB_' not in report
    assert 'local configuration' not in report
    assert str(project) not in report
    if expected_code:
        assert 'Status: PASS' not in report


def test_core_spawn_failure_is_safe_and_still_reports_action_and_exit(project, monkeypatch):
    spec = importlib.util.spec_from_file_location('core_toolkit', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = project
    monkeypatch.setattr(module.sys, 'executable', str(project / 'missing python'))
    assert module.main(['core', '--action', 'start']) == 2
    report = next((project / 'logs').glob('*-core.log')).read_text()
    assert 'Action: start' in report and 'Status: FAIL' in report and 'Exit code: 2' in report
    assert 'CORE_CONTROL_SPAWN_FAILED' in report
    assert str(project) not in report


def test_core_suite_discovers_sorted_core_targets_and_keeps_runtime_suite(project):
    tests = project / 'tests/server'
    tests.mkdir(parents=True)
    (tests / 'test_core_b.py').write_text('def test_b():\n assert True\n')
    (tests / 'test_core_a.py').write_text('def test_a():\n assert True\n')
    for name in ['test_runtime', 'test_ports', 'test_benchmark','test_persistent_adapter',
                 'test_persistent_service','test_persistent_api','test_persistent_benchmark']:
        (tests / (name + '.py')).write_text('def test_runtime_contract():\n assert True\n')
    (tests / 'test_unrelated.py').write_text('def test_unrelated():\n assert False\n')
    result = invoke(project, 'test', '--suite', 'core')
    assert result.returncode == 0, result.stdout + result.stderr
    report = (project / 'logs/latest-test-report.txt').read_text()
    assert 'Suite: core' in report and 'Passed: 6' in report
    result = invoke(project, 'test', '--suite', 'runtime')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Passed: 7' in (project / 'logs/latest-test-report.txt').read_text()


def test_empty_core_suite_cannot_report_pass(project):
    result = invoke(project, 'test', '--suite', 'core')
    assert result.returncode == 2
    report = (project / 'logs/latest-test-report.txt').read_text()
    assert 'Status: FAIL' in report
    assert 'CORE_TEST_TARGETS_MISSING' in report


def test_failure_summary_preserves_allowlisted_type_location_and_numbers_only():
    spec = importlib.util.spec_from_file_location('core_toolkit', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = ('/private/home/alice/tests/server/test_core_api.py:125: AssertionError\n'
            'E assert "private-assertion" == "other"\n'
            'UntrustedCustomException: private-value [WinError 10048] [Errno 98]')
    summary = module.safe_error_summary(text)
    assert 'AssertionError' in summary
    assert 'test_core_api.py:125' in summary
    assert 'WinError 10048' in summary and 'Errno 98' in summary
    for forbidden in ['alice', '/private', 'private-assertion', 'private-value', 'UntrustedCustomException']:
        assert forbidden not in summary


@pytest.mark.parametrize(('name', 'action'), CORE_BATS.items())
def test_core_bat_wrappers_keep_bootstrap_quotes_crlf_exit_and_acceptance_scope(name, action):
    path = SOURCE.parent / (name + '.bat')
    assert path.is_file()
    raw = path.read_bytes()
    content = raw.decode('ascii')
    assert b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b'')
    required = [
        'setlocal DisableDelayedExpansion', 'chcp 65001 >nul', 'set "PYTHONUTF8=1"',
        'set "PYTHONIOENCODING=utf-8"', 'set "EGO_ROOT=%~dp0..\\.."', 'pushd "%EGO_ROOT%"',
        'if errorlevel 1 goto root_error', 'set "EGO_PYTHON=%EGO_ROOT%\\.venv\\Scripts\\python.exe"',
        'if not exist "%EGO_PYTHON%" goto venv_error', 'if errorlevel 1 goto python_error',
        'sys.version_info[:2] == (3,12)', 'sys.maxsize.bit_length() == 63',
        '"%EGO_PYTHON%" "%~dp0runner.py" core --action ' + action,
        'set "EGO_EXIT=%ERRORLEVEL%"', 'set "EGO_BOOT_REASON=VENV_MISSING"',
        'set "EGO_BOOT_REASON=PYTHON_312_X64_MISSING_OR_INVALID"',
        'set "EGO_BOOT_REASON=PROJECT_ROOT_UNAVAILABLE"', ':bootstrap_log_error',
        'echo Exit code: %EGO_EXIT%', 'pause >nul', 'exit /b %EGO_EXIT%',
        'Current Windows BAT entrypoint: EXECUTED', 'M1 native Windows acceptance: PENDING',
    ]
    for expected in required:
        assert expected in content
    assert 'Native Windows BAT/cmd execution: NOT_TESTED' not in content
    assert '>> "%EGO_BOOT_LOG%" echo Current Windows BAT entrypoint: EXECUTED' in content
    assert '>> "%EGO_BOOT_LOG%" echo M1 native Windows acceptance: PENDING' in content
    assert 'del ' not in content.lower() and 'rmdir' not in content.lower()


def test_diagnostics_core_report_retains_only_safe_structured_fields(project):
    logs = project / 'logs'
    logs.mkdir()
    payload = {
        'status': 'FAIL', 'code': 'CORE_RUNTIME_START_FAILED', 'errno': 98, 'winerror': 10048,
        'token': 'private-token', 'text': 'private-generation-text',
        'core': {'stage': 'M1', 'runtime': 'idle', 'storage': 'ready',
                 'active_generation': 'b3c8d1e2-9058-4da2-861a-16c5ad417835',
                 'recovered_generations': 2,
                 'last_runtime_error': {'code': 'PORT_IN_USE', 'errno': 98, 'winerror': 10048,
                                        'message': 'private-exception-message'},
                 'private': 'private-field', 'scope': 'private-description'},
    }
    (logs / 'latest-core-report.json').write_text(json.dumps(payload))
    (logs / '20261019-120000-000001-core-server.log').write_text('private-daemon-output')
    result = invoke(project, 'diagnostics')
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((logs / 'latest-diagnostics.json').read_text())
    core = report['reports']['latest-core-report.json']
    assert core['status'] == 'FAIL' and core['code'] == 'CORE_RUNTIME_START_FAILED'
    assert core['errno'] == 98 and core['winerror'] == 10048
    assert core['core'] == {
        'stage': 'M1', 'runtime': 'idle', 'storage': 'ready',
        'active_generation': 'b3c8d1e2-9058-4da2-861a-16c5ad417835', 'recovered_generations': 2,
        'last_runtime_error': {'code': 'PORT_IN_USE', 'errno': 98, 'winerror': 10048},
    }
    assert report['m1_native_windows_acceptance'] == 'PENDING'
    assert report['current_host'] in {'Windows', 'Linux', 'Darwin', 'OTHER'}
    assert report['native_windows_execution'] == 'NOT_TESTED'
    for path in [logs / 'latest-diagnostics.json', *logs.glob('*-diagnostics.log')]:
        content = path.read_text()
        for forbidden in ['private-token', 'private-generation-text', 'private-exception-message',
                          'private-field', 'private-description', 'private-daemon-output']:
            assert forbidden not in content


@pytest.mark.parametrize('content', ['{broken', '{"private":"' + 'x' * 9000 + '"}'], ids=['malformed', 'oversized'])
def test_diagnostics_core_report_is_bounded_and_handles_invalid_json(project, content):
    logs = project / 'logs'
    logs.mkdir()
    (logs / 'latest-core-report.json').write_text(content)
    result = invoke(project, 'diagnostics')
    assert result.returncode == 0
    report = json.loads((logs / 'latest-diagnostics.json').read_text())
    assert report['reports']['latest-core-report.json'] == {
        'status': 'BLOCKED', 'code': 'CORE_DIAGNOSTIC_REPORT_INVALID'}
    assert len(json.dumps(report['reports'])) < 500
