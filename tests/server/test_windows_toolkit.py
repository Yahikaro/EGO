"""Portable tests of the stdlib Windows toolkit (native BAT execution excluded)."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'tools/windows/runner.py'


def symlink_or_skip(link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError:
        pytest.skip('OS does not permit symlink fixture; native junction behavior NOT_TESTED')


def toolkit():
    assert SOURCE.is_file(), 'Windows toolkit runner has not been implemented'
    spec = importlib.util.spec_from_file_location('windows_toolkit', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'EGO space Кириллица ! &'
    (root / 'tools/windows').mkdir(parents=True)
    assert SOURCE.is_file(), 'Windows toolkit runner has not been implemented'
    shutil.copy2(SOURCE, root / 'tools/windows/runner.py')
    (root / 'scripts').mkdir()
    (root / 'scripts/sqlcipher_check.py').write_text(
        "def check_sqlcipher():\n return {'status':'PASS','reason':'VERIFIED','cipher_version':'4.test'}\n")
    (root / 'pyproject.toml').write_text('[project]\nversion="0.0.3"\n')
    return root


def invoke(project, *args):
    return subprocess.run([sys.executable, str(project / 'tools/windows/runner.py'), *args],
                          cwd=project.parent, capture_output=True, text=True, timeout=40)


@pytest.mark.parametrize(('body', 'code', 'status'), [
    ('def test_ok():\n assert True\n', 0, 'PASS'),
    ('def test_bad():\n assert False\n', 1, 'FAIL'),
    ('import pytest\ndef test_skip():\n pytest.skip("explicit fixture unavailable")\n', 0, 'PASS'),
])
def test_real_pytest_reports_and_exit_codes(project, body, code, status):
    (project / 'case.py').write_text(body)
    result = invoke(project, 'test', '--suite', 'all', '--test-path', 'case.py')
    assert result.returncode == code, result.stdout + result.stderr
    report = (project / 'logs/latest-test-report.txt').read_text()
    assert f'Status: {status}' in report
    assert 'EGO: 0.0.3' in report
    assert 'Python:' in report and 'Windows:' in report
    assert 'Duration:' in report
    assert list((project / 'logs').glob('*-all.log'))
    xml = ET.parse(project / 'logs/latest-test-report.xml')
    assert xml.findall('.//testcase')
    if 'skip' in body:
        assert 'explicit fixture unavailable' in report
    if code:
        assert 'test_bad' in report


def test_sqlcipher_blocked_makes_skipped_security_suite_fail(project):
    (project / 'scripts/sqlcipher_check.py').write_text(
        "def check_sqlcipher():\n return {'status':'BLOCKED','reason':'SQLCIPHER_MISSING','cipher_version':None}\n")
    (project / 'case.py').write_text('import pytest\ndef test_crypto():\n pytest.skip("missing driver")\n')
    result = invoke(project, 'test', '--suite', 'security', '--test-path', 'case.py')
    assert result.returncode != 0
    assert 'SQLCIPHER_MISSING' in (project / 'logs/latest-test-report.txt').read_text()
    assert 'Status: FAIL' in (project / 'logs/latest-test-report.txt').read_text()


def test_report_parser_rejects_malformed_missing_empty_and_collection_errors(tmp_path):
    module = toolkit()
    source = tmp_path / 'junit.xml'
    assert module.parse_junit(source)['valid'] is False
    for content in ['<broken', '<testsuites/>', '<testsuite><testcase name="collection"><error message="syntax"/></testcase></testsuite>']:
        source.write_text(content)
        report = module.parse_junit(source)
        assert not report['valid'] or report['failed'] == 1


def test_redaction_covers_credentials_paths_and_no_environment_dump():
    module = toolkit()
    secret = 'token=topsecret password: hunter2 Authorization: Bearer abcd sk-proj-123456789 /home/alice/private.txt C:\\Users\\Alice\\secret.txt https://alice:hunter2@host/path'
    cleaned = module.redact(secret)
    for value in ['topsecret', 'hunter2', 'abcd', '123456789', 'Alice', 'alice', 'private.txt']:
        assert value not in cleaned
    assert module.redact('test_example.py::test_ok') == 'test_example.py::test_ok'


def test_cleanup_only_allowlisted_cache_and_skips_links(tmp_path):
    module = toolkit()
    root = tmp_path / 'project'
    root.mkdir()
    for folder in ['src/__pycache__', '.pytest_cache', '.venv/__pycache__', 'logs/__pycache__', 'data/__pycache__', 'models/__pycache__', 'user/__pycache__', 'config/__pycache__']:
        path = root / folder
        path.mkdir(parents=True)
        (path / 'keep.pyc').write_bytes(b'cache')
    (root / 'src/loose.pyc').write_bytes(b'cache')
    (root / 'src/source.py').write_text('x=1')
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'secret.pyc').write_bytes(b'private')
    symlink_or_skip(root / 'linked', external, target_is_directory=True)
    assert module.clean_cache(root, confirmed=False) == 0
    assert (root / 'src/loose.pyc').exists()
    assert module.clean_cache(root, confirmed=True) >= 3
    assert not (root / 'src/__pycache__').exists()
    assert not (root / '.pytest_cache').exists()
    assert not (root / 'src/loose.pyc').exists()
    for folder in ['.venv', 'logs', 'data', 'models', 'user', 'config']:
        assert (root / folder / '__pycache__/keep.pyc').exists()
    assert (external / 'secret.pyc').exists()
    assert (root / 'src/source.py').exists()


def test_benchmark_without_model_is_blocked(project):
    result = invoke(project, 'benchmark', '--non-interactive')
    assert result.returncode != 0
    assert 'BLOCKED' in result.stdout
    assert 'MODEL_NOT_PROVIDED' in result.stdout
    assert 'not model performance' in result.stdout.lower()


def test_diagnostics_only_owned_sanitized_content(project):
    logs = project / 'logs'
    logs.mkdir()
    (logs / 'private.log').write_text('private confidential contents')
    (logs / 'latest-test-report.txt').write_text('Status: FAIL\ntoken=topsecret\n/home/alice/private\n')
    (project / '.env').write_text('PASSWORD=donotread')
    result = invoke(project, 'diagnostics')
    assert result.returncode == 0, result.stderr
    payload = (logs / 'latest-diagnostics.json').read_text()
    for forbidden in ['confidential', 'topsecret', 'alice', 'donotread']:
        assert forbidden not in payload
    data = json.loads(payload)
    assert 'local_network' in data
    assert 'native_windows_execution' in data
    assert data['native_windows_execution'] == 'NOT_TESTED'


def test_test_collection_error_is_failed_report(project):
    (project / 'case.py').write_text('this is invalid python !\n')
    result = invoke(project, 'test', '--test-path', 'case.py')
    assert result.returncode == 2
    report = (project / 'logs/latest-test-report.txt').read_text()
    assert 'Status: FAIL' in report
    assert 'FAILED' in report and 'case' in report


def test_junit_output_excludes_private_captured_output_and_preserves_escaped_xml(project):
    (project / 'case.py').write_text('import pytest\ndef test_secret():\n print("private uncategorized content")\n pytest.skip("token=topsecret <reason> & safe")\n')
    result = invoke(project, 'test', '--test-path', 'case.py')
    assert result.returncode == 0
    for path in (project / 'logs').iterdir():
        text = path.read_text()
        assert 'topsecret' not in text
        assert 'private uncategorized content' not in text
    ET.parse(project / 'logs/latest-test-report.xml')


def test_cleanup_skips_link_nested_in_cache_and_preserves_protected_subtrees(tmp_path):
    module = toolkit()
    root = tmp_path / 'root'
    protected = root / 'src/__pycache__/data'
    protected.mkdir(parents=True)
    (protected / 'user.bin').write_bytes(b'precious')
    external = tmp_path / 'outside'
    external.mkdir()
    (external / 'keep.pyc').write_bytes(b'cache')
    linked_cache = root / 'linked/__pycache__'
    linked_cache.parent.mkdir()
    symlink_or_skip(linked_cache, external, target_is_directory=True)
    module.clean_cache(root, confirmed=True)
    assert (protected / 'user.bin').exists()
    assert (external / 'keep.pyc').exists()


def test_setup_python_rejection_never_creates_or_deletes_venv(project, monkeypatch):
    module = toolkit()
    original_root = module.ROOT
    module.ROOT = project
    try:
        monkeypatch.setattr(module.sys, 'version_info', (3, 11, 0))
        messages = []
        assert module.setup(messages.append) == 2
        assert not (project / '.venv').exists()
        assert any('3.12 x64' in text for text in messages)
    finally:
        module.ROOT = original_root


def test_log_symlink_is_rejected_without_writing_external_file(project, tmp_path):
    external = tmp_path / 'private'
    external.mkdir()
    symlink_or_skip(project / 'logs', external, target_is_directory=True)
    result = invoke(project, 'diagnostics')
    assert result.returncode != 0
    assert list(external.iterdir()) == []


def test_missing_target_still_creates_failed_reports(project):
    result = invoke(project, 'test', '--test-path', 'absent.py')
    assert result.returncode == 2
    text = (project / 'logs/latest-test-report.txt').read_text()
    assert 'Status: FAIL' in text
    assert 'TEST_TARGET_MISSING_OR_OUTSIDE_PROJECT' in text
    ET.parse(project / 'logs/latest-test-report.xml')


def test_failure_assertion_values_are_not_copied_to_reports(project):
    (project / 'case.py').write_text('def test_private_assertion():\n assert "unclassified-private-value" == "other"\n')
    result = invoke(project, 'test', '--test-path', 'case.py')
    assert result.returncode == 1
    for path in (project / 'logs').iterdir():
        assert 'unclassified-private-value' not in path.read_text()
    assert 'test_private_assertion' in (project / 'logs/latest-test-report.txt').read_text()


def test_report_dangling_symlink_never_creates_external_target(project, tmp_path):
    logs = project / 'logs'
    logs.mkdir()
    external = tmp_path / 'not-created.txt'
    symlink_or_skip(logs / 'latest-diagnostics.json', external)
    result = invoke(project, 'diagnostics')
    assert result.returncode != 0
    assert not external.exists()


def test_setup_rejects_non_x64_architecture(project, monkeypatch):
    module = toolkit()
    monkeypatch.setattr(module, 'ROOT', project)
    monkeypatch.setattr(module.sys, 'version_info', (3, 12, 0))
    monkeypatch.setattr(module.platform, 'machine', lambda: 'ARM64')
    messages = []
    assert module.setup(messages.append) == 2
    assert not (project / '.venv').exists()


def test_nine_bat_artifacts_are_ascii_crlf_not_native_execution():
    names = ['01_SETUP', '02_ENV_CHECK', '03_RUN_ALL_TESTS', '04_RUN_RUNTIME_TESTS',
             '05_RUN_SECURITY_TESTS', '06_RUN_BENCHMARK', '07_DIAGNOSTICS', '08_OPEN_LOGS', '09_CLEAN_CACHE']
    for name in names:
        content = (SOURCE.parent / (name + '.bat')).read_bytes()
        content.decode('ascii')
        assert b'\r\n' in content
        assert b'\n' not in content.replace(b'\r\n', b'')


def test_environment_missing_dependencies_fails_without_environment_dump(project):
    (project / 'scripts/check_environment.py').write_text(
        "def inventory():\n return {'os':'Windows','python':'3.12.0','packages': {'pytest': None}, 'environment': {'SECRET':'private'}}\n")
    result = invoke(project, 'environment')
    assert result.returncode != 0
    assert 'DEPENDENCIES_MISSING' in result.stdout
    assert 'private' not in result.stdout


def test_diagnostics_has_bounded_sanitized_owned_log_tails_only(project):
    logs = project / 'logs'
    logs.mkdir()
    (logs / '20261019-120000-000001-runtime.log').write_text('x' * 10000 + '\nStatus: FAIL\ntoken=topsecret\n')
    (logs / 'private.log').write_text('private excluded content')
    result = invoke(project, 'diagnostics')
    assert result.returncode == 0
    report = json.loads((logs / 'latest-diagnostics.json').read_text())
    tails = report['owned_log_tails']
    assert tails
    assert all(len(value.encode()) <= 8500 for value in tails.values())
    assert 'Status: FAIL' in str(tails)
    assert 'topsecret' not in str(tails)
    assert 'private excluded content' not in str(tails)


def test_json_credential_redaction_preserves_valid_json_and_token_metrics():
    module = toolkit()
    payload = {'password': 'not-for-logs', 'access_token': 'private-credential',
               'Authorization': 'Bearer abcdef', 'tokens_predicted': 128,
               'url': 'https://alice:secret@example.com/path'}
    cleaned = json.loads(module.redact(json.dumps(payload)))
    assert cleaned['tokens_predicted'] == 128
    assert 'not-for-logs' not in str(cleaned)
    assert 'private-credential' not in str(cleaned)
    assert 'abcdef' not in str(cleaned)
    assert 'alice' not in str(cleaned)


def test_failure_summary_retains_safe_runtime_codes_and_numeric_os_errors(project):
    (project / 'case.py').write_text(
        'def test_bind():\n raise RuntimeError("RuntimeFailure: PORT_IN_USE [WinError 10048] [Errno 98] private-value")\n')
    result = invoke(project, 'test', '--suite', 'runtime', '--test-path', 'case.py')
    assert result.returncode == 1
    report = (project / 'logs/latest-test-report.txt').read_text()
    assert 'PORT_IN_USE' in report and 'WinError 10048' in report and 'Errno 98' in report
    assert 'private-value' not in report


def fake_setup_python(project, *, binary_present=False, pip_code=0):
    executable = project / '.venv/Scripts/python.exe'
    executable.parent.mkdir(parents=True)
    script = ('#!' + sys.executable + '\nimport json,sys\nfrom pathlib import Path\n'
              + 'root=Path(__file__).resolve().parents[2]\n'
              + "if 'binding-state' in sys.argv:\n print(json.dumps({'sqlcipher3': None, 'sqlcipher3-binary': "
              + ("'0.5.4'" if binary_present else 'None') + '}))\n'
              + "elif '-m' in sys.argv and 'pip' in sys.argv:\n with (root/'pip-called.txt').open('a') as f: f.write(' '.join(sys.argv[1:])+'\\n')\n raise SystemExit("
              + str(pip_code) + ')\n')
    executable.write_text(script)
    executable.chmod(0o755)
    (project / 'requirements.txt').write_text('pytest==8.3.5\n')
    (project / 'requirements-windows.txt').write_text('-r requirements.txt\nsqlcipher3==0.6.4\n')


@pytest.mark.skipif(os.name == 'nt', reason='POSIX fake executable harness; native Windows setup NOT_TESTED')
def test_setup_blocks_sqlcipher_namespace_conflict_before_pip(project, monkeypatch):
    fake_setup_python(project, binary_present=True)
    module = toolkit()
    monkeypatch.setattr(module, 'ROOT', project)
    messages = []
    assert module.setup(messages.append) != 0
    assert not (project / 'pip-called.txt').exists()
    assert any('SQLCIPHER_NAMESPACE_CONFLICT' in text for text in messages)
    assert any('fresh' in text.lower() for text in messages)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX fake executable harness; native Windows setup NOT_TESTED')
def test_setup_pip_failure_has_explicit_stage_and_exit(project, monkeypatch):
    fake_setup_python(project, pip_code=17)
    module = toolkit()
    monkeypatch.setattr(module, 'ROOT', project)
    messages = []
    assert module.setup(messages.append) == 17
    assert any('BASE_DEPENDENCIES_INSTALL_FAILED' in text and 'requirements.txt' in text for text in messages)


def test_diagnostics_loopback_probes_and_previous_error_numbers(project):
    logs = project / 'logs'
    logs.mkdir()
    (logs / 'latest-test-report.txt').write_text('FAILED test_bind: PORT_IN_USE WinError 10048 Errno 98\n')
    result = invoke(project, 'diagnostics')
    assert result.returncode == 0
    data = json.loads((logs / 'latest-diagnostics.json').read_text())
    probes = data['local_network']['loopback_bind_probes']
    assert {probe['port'] for probe in probes} == {18081, 18082, 19000}
    assert all(probe['scope'] == 'loopback_only' for probe in probes)
    assert 10048 in data['local_network']['previous_error_numbers']


def test_fixture_benchmark_mode_runs_real_focused_tests_and_labels_scope(project):
    test = project / 'tests/server/test_benchmark.py'
    test.parent.mkdir(parents=True)
    test.write_text('def test_contract_fixture():\n assert True\n')
    result = invoke(project, 'benchmark', '--fixture-check', '--non-interactive')
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((project / 'logs/latest-benchmark-report.json').read_text())
    assert report['scope'] == 'fixture_contract_checks_only'
    assert report['real_model_performance'] == 'NOT_TESTED'
    assert (project / 'logs/latest-test-report.xml').exists()
