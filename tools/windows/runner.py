"""EGO Windows Developer Toolkit; standard library only at import time.

BAT entrypoints select Python; all paths derive from this file, never the cwd.
CLI: setup, environment, test --suite all|runtime|security|core [--test-path PATH],
core --action setup|start|status|stop|smoke|configure,
benchmark [--binary PATH --model PATH --non-interactive | --fixture-check], diagnostics, logs,
clean [--yes]. Direct test-path overrides are useful for focused local checks.
Native Windows BAT/cmd execution: NOT_TESTED in the portable test environment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
PROTECTED = {'.venv', 'venv', 'logs', 'data', 'model', 'models', 'user', 'users',
             'userdata', 'user_data', 'config', 'configs', '.git'}


def redact(value):
    """Best-effort additional defense; collection itself uses strict allowlists."""
    text = str(value)
    text = re.sub(r'https?://[^\s"\'<>]+', '[URL]', text, flags=re.I)
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+[^\s"\',;}\]]+', '[CREDENTIAL]', text)
    credential = r"(?i)(\b[\w-]*(?:token|password|passwd|secret|api[_-]?key|private[_-]?key)[\"']?\s*[:=]\s*)(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;}\]]+)"
    def hide(match):
        value = match.group(2)
        quote = value[0] if value and value[0] in {'"', "'"} else ''
        return match.group(1) + quote + '[CREDENTIAL]' + quote
    text = re.sub(credential, hide, text)
    text = re.sub(r'\bsk-(?:proj-)?[A-Za-z0-9_-]+', '[CREDENTIAL]', text)
    text = re.sub(r'(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n<>"|]*', '[PATH]', text)
    text = re.sub(r'(?<![\w:])/(?:[^\s<>"\']+)', '[PATH]', text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[PRIVATE KEY]', text, flags=re.S)
    return text


def linked(path):
    """Include Windows junctions and all reparse points, even on Python <3.12."""
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0)
                                        & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
    except OSError:
        return True


def safe_logs():
    logs = ROOT / 'logs'
    if logs.is_symlink() or (logs.exists() and linked(logs)):
        raise RuntimeError('LOGS_REPARSE_POINT_BLOCKED')
    logs.mkdir(exist_ok=True)
    return logs


def save(path, text, sanitize=True):
    if path.is_symlink() or (path.exists() and linked(path)):
        raise RuntimeError('OUTPUT_REPARSE_POINT_BLOCKED')
    path.write_text(redact(text) if sanitize else text, encoding='utf-8')


def load_script(name):
    path = ROOT / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location('ego_toolkit_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sqlcipher_check():
    try:
        result = load_script('sqlcipher_check').check_sqlcipher()
        if result.get('status') not in {'PASS', 'FAIL', 'BLOCKED'}:
            raise ValueError('invalid SQLCipher status')
        return {k: redact(result.get(k)) for k in ('status', 'reason', 'cipher_version')}
    except Exception as exc:
        return {'status': 'BLOCKED', 'reason': 'SQLCIPHER_CHECK_UNAVAILABLE_' + type(exc).__name__,
                'cipher_version': None}


def version():
    try:
        import tomllib
        return tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    except (OSError, ValueError, KeyError):
        return 'UNKNOWN'


def safe_error_summary(text):
    codes = {'MODEL_NOT_PROVIDED', 'INVALID_GGUF', 'RUNTIME_NOT_PROVIDED', 'STARTUP_CANCELLED',
             'START_IN_PROGRESS', 'RUNTIME_START_FAILED', 'RUNTIME_EXITED', 'STARTUP_TIMEOUT',
             'RUNTIME_BUSY', 'RUNTIME_UNAVAILABLE', 'GENERATION_REJECTED', 'INVALID_STREAM',
             'GENERATION_FAILED', 'STREAM_INTERRUPTED', 'RUNTIME_IO_ERROR', 'PORT_IN_USE',
             'PORT_BIND_FAILED', 'PORT_PROBE_FAILED'}
    found = sorted(code for code in codes if re.search(r'\b' + code + r'\b', text))
    # Preserve diagnostic structure without assertion values, messages or directories.
    kinds = {'AssertionError', 'ValueError', 'TypeError', 'RuntimeError', 'OSError',
             'FileNotFoundError', 'PermissionError', 'TimeoutError', 'ImportError',
             'ModuleNotFoundError', 'SyntaxError', 'KeyError', 'AttributeError',
             'IndexError', 'NameError', 'UnboundLocalError', 'ConnectionError',
             'ConnectionRefusedError', 'BrokenPipeError', 'ZeroDivisionError',
             'UnicodeError', 'UnicodeDecodeError', 'UnicodeEncodeError'}
    found.extend(sorted(kind for kind in kinds if re.search(r'\b' + kind + r'\b', text)))
    found.extend(name + ':' + str(int(line)) for name, line in re.findall(
        r'\b(test_[A-Za-z0-9_]{1,100}\.py):(\d{1,7})\b', text))
    found.extend(name + ':' + str(int(line)) for name, line in re.findall(
        r'\b(test_[A-Za-z0-9_]{1,100}\.py)["\'], line (\d{1,7})\b', text))
    for kind, number in re.findall(r'(?i)\b(WinError|Errno)\s*(?:[:=]\s*)?(\d{1,6})\b', text):
        found.append(('WinError' if kind.lower() == 'winerror' else 'Errno') + ' ' + str(int(number)))
    return '; '.join(dict.fromkeys(found)) or 'TEST_FAILURE_DETAILS_OMITTED_FOR_PRIVACY'


def binding_state():
    result = {}
    for name in ('sqlcipher3', 'sqlcipher3-binary'):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def parse_junit(path):
    report = {'valid': False, 'passed': 0, 'failed': 0, 'skipped': 0, 'details': [], 'cases': []}
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('oversized JUnit')
        tree = ET.parse(path)
        cases = tree.findall('.//testcase')
        if tree.getroot().tag == 'testcase':
            cases = [tree.getroot()]
        if not cases:
            return report
        for case in cases:
            name = redact(case.get('classname', '') + '::' + case.get('name', 'unnamed'))
            failure = case.find('failure')
            error = case.find('error')
            skipped = case.find('skipped')
            problem = failure if failure is not None else error
            state = 'failed' if problem is not None else ('skipped' if skipped is not None else 'passed')
            report[state] += 1
            reason = ''
            if problem is not None or skipped is not None:
                node = problem if problem is not None else skipped
                # No stack locals, captured stdout/stderr, or assertion source copied.
                reason = (safe_error_summary(node.get('message', '') + '\n' + (node.text or '')) if problem is not None else
                          redact(node.get('message', '') or (node.text or '').strip().split('\n')[-1])[:2000])
                report['details'].append(f'{state.upper()} {name}: {reason}')
            report['cases'].append((name, state, reason))
        report['valid'] = True
        return report
    except (OSError, ET.ParseError, ValueError):
        return report


def write_junit(path, report, elapsed, critical):
    failures = report['failed'] + int(critical is not None)
    suite = ET.Element('testsuite', name='EGO', tests=str(len(report['cases']) + int(critical is not None)),
                       failures=str(failures), skipped=str(report['skipped']), time=f'{elapsed:.3f}')
    for name, state, reason in report['cases']:
        case = ET.SubElement(suite, 'testcase', name=name)
        if state in {'failed', 'skipped'}:
            ET.SubElement(case, 'failure' if state == 'failed' else 'skipped', message=reason)
    if critical:
        case = ET.SubElement(suite, 'testcase', name='toolkit_critical_check')
        ET.SubElement(case, 'failure', message=redact(critical))
    save(path, ET.tostring(suite, encoding='unicode') + '\n', sanitize=False)


def run_tests(args, emit):
    targets = args.test_path or {'all': ['tests/server', 'tests/acceptance'],
                                'runtime': ['tests/server/test_runtime.py', 'tests/server/test_ports.py', 'tests/server/test_benchmark.py',
                                            'tests/server/test_persistent_adapter.py','tests/server/test_persistent_service.py',
                                            'tests/server/test_persistent_api.py','tests/server/test_persistent_benchmark.py'],
                                'security': ['tests/acceptance', 'tests/server/test_core_keys.py',
                                             'tests/server/test_core_store.py', 'tests/server/test_core_api.py'],
                                'core': [str(path.relative_to(ROOT)) for path in sorted(
                                    [*ROOT.glob('tests/server/test_core*.py'),*ROOT.glob('tests/server/test_persistent*.py'),*ROOT.glob('tests/server/test_m13*.py')])]}[args.suite]
    target_error = None if targets else 'CORE_TEST_TARGETS_MISSING'
    for target in targets:
        candidate = (ROOT / target).resolve()
        if not candidate.is_relative_to(ROOT) or not candidate.exists():
            target_error = 'TEST_TARGET_MISSING_OR_OUTSIDE_PROJECT'
    started = time.perf_counter()
    # Raw child output is deliberately discarded: assertion output can contain secrets.
    # JUnit is temporary, then rebuilt from an allowlisted subset of fields.
    with tempfile.TemporaryDirectory(prefix='ego-toolkit-') as temp:
        source = Path(temp) / 'junit.xml'
        cmd = [sys.executable, '-m', 'pytest', *targets, '-q', '-ra', '--tb=short',
               '--capture=fd', '-o', 'junit_logging=no', '-o', 'junit_log_passing_tests=false',
               '--junitxml=' + str(source)]
        emit('Running pytest; raw assertion output is suppressed to protect private values.')
        try:
            if target_error:
                exit_code = 2
            else:
                child = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                exit_code = child.returncode
        except OSError:
            exit_code = 2
        report = parse_junit(source)
    cipher = sqlcipher_check() if args.suite in {'all', 'security'} else None
    critical = None
    if not report['valid']:
        critical = target_error or 'JUNIT_MISSING_EMPTY_OR_MALFORMED'
    if cipher and cipher['status'] != 'PASS':
        critical = 'SQLCIPHER_' + cipher['status'] + ': ' + cipher['reason']
    if exit_code or report['failed'] or critical:
        exit_code = exit_code if exit_code > 0 else 1
    else:
        exit_code = 0
    elapsed = time.perf_counter() - started
    status = 'FAIL' if exit_code else 'PASS'
    lines = [f'Status: {status}', f'EGO: {version()}', f'Windows: {platform.system()} {platform.release()} {platform.version()}',
             f'Python: {platform.python_version()} {platform.architecture()[0]}',
             f'Suite: {args.suite}', f'Exit code: {exit_code}', f'Passed: {report["passed"]}', f'Failed: {report["failed"]}',
             f'Skipped: {report["skipped"]}', f'Duration: {elapsed:.3f}s',
             'Native Windows BAT/cmd execution: NOT_TESTED',
             'Fixture checks validate behavior; not model performance.']
    if cipher:
        lines.append('SQLCipher: ' + json.dumps(cipher, ensure_ascii=True))
    lines.extend(report['details'])
    if critical:
        lines.append('CRITICAL: ' + critical)
    text = '\n'.join(lines) + '\n'
    logs = safe_logs()
    save(logs / 'latest-test-report.txt', text)
    write_junit(logs / 'latest-test-report.xml', report, elapsed, critical)
    emit(text)
    return exit_code


def environment():
    try:
        raw = load_script('check_environment').inventory()
    except Exception as exc:
        raw = {'inventory_status': 'BLOCKED_' + type(exc).__name__}
    # Keep package versions/known GPU descriptors; never environment variables or host/user identity.
    output = {k: raw[k] for k in ('os', 'architecture', 'python', 'packages', 'gpu', 'windows_dpapi', 'inventory_status') if k in raw}
    required = ('fastapi', 'pydantic', 'httpx', 'cryptography', 'uvicorn', 'pytest', 'psutil')
    packages = raw.get('packages', {})
    output['missing_dependencies'] = [name for name in required if not packages.get(name)]
    output['dependency_status'] = 'DEPENDENCIES_MISSING' if output['missing_dependencies'] else 'PASS'
    output['sqlcipher'] = sqlcipher_check()
    output['cuda_runtime'] = 'NOT_VERIFIED: nvidia-smi CUDA is driver capability, not installed runtime'
    output['cuda_toolkit_nvcc'] = 'PRESENT_UNVERIFIED' if shutil.which('nvcc') else 'OPTIONAL_NOT_FOUND'
    output['native_windows_execution'] = 'NOT_TESTED'
    return json.loads(redact(json.dumps(output, ensure_ascii=True)))


def setup(emit):
    if (sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32
            or platform.machine().lower() not in {'amd64', 'x86_64'}):
        emit('FAIL: Python 3.12 x64 is required. Install it and run setup again.')
        return 2
    venv = ROOT / '.venv'
    if linked(venv) and venv.exists():
        emit('FAIL: VENV_REPARSE_POINT_BLOCKED')
        return 2
    python = venv / 'Scripts/python.exe'
    if not python.exists():
        result = subprocess.run([sys.executable, '-m', 'venv', str(venv)], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            emit('FAIL: VENV_CREATION_FAILED')
            return result.returncode
    check = subprocess.run([str(python), '-c', "import sys,platform;raise SystemExit(0 if sys.version_info[:2]==(3,12) and sys.maxsize>2**32 and platform.machine().lower() in ('amd64','x86_64') else 2)"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        emit('FAIL: Existing .venv must use Python 3.12 x64; it was not deleted.')
        return check.returncode
    requirements = ROOT / 'requirements-windows.txt'
    if not requirements.is_file():
        emit('FAIL: REQUIREMENTS_WINDOWS_MISSING')
        return 2
    probe = subprocess.run([str(python), str(Path(__file__).resolve()), 'binding-state'],
                           cwd=ROOT, capture_output=True, text=True, timeout=10)
    try:
        state = json.loads(probe.stdout)
        if probe.returncode or not isinstance(state, dict):
            raise ValueError('binding metadata unavailable')
    except (ValueError, TypeError):
        emit('FAIL: SQLCIPHER_DISTRIBUTION_CHECK_FAILED; no packages were changed.')
        return 2
    if state.get('sqlcipher3-binary'):
        emit('FAIL: SQLCIPHER_NAMESPACE_CONFLICT: sqlcipher3-binary shares the sqlcipher3 namespace. '
             'Use a fresh Windows venv with sqlcipher3; preserve/move the old venv yourself. '
             'No packages were installed or uninstalled.')
        return 2
    base = ROOT / 'requirements.txt'
    if not base.is_file():
        emit('FAIL: BASE_REQUIREMENTS_MISSING: requirements.txt')
        return 2
    for manifest, stage in ((base, 'BASE_DEPENDENCIES'), (requirements, 'SQLCIPHER_WINDOWS_BINDING')):
        emit('Installing stage: ' + stage + ' from ' + manifest.name + ' (binary wheels only).')
        result = subprocess.run([str(python), '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(manifest)],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            emit('FAIL: ' + stage + '_INSTALL_FAILED: ' + manifest.name
                 + (' (sqlcipher3 Windows binding)' if stage == 'SQLCIPHER_WINDOWS_BINDING' else '')
                 + '; exit=' + str(result.returncode)
                 + '. Verify CPython 3.12 x64 wheel availability and the configured package index. '
                   'Raw pip output is omitted because index URLs may contain credentials.')
            return result.returncode
    # The setup process may use the system interpreter; verification must use .venv.
    result = subprocess.run([str(python), str(Path(__file__).resolve()), 'environment'], cwd=ROOT)
    return result.returncode


def clean_cache(root, confirmed=False):
    if not confirmed or linked(root):
        return 0
    removed = 0
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        path = Path(current)
        # Prune early: never enumerate virtualenv, logs, private data or links.
        dirs[:] = [name for name in dirs if name.casefold() not in PROTECTED
                   and not linked(path / name)]
        for name in list(dirs):
            candidate = path / name
            if name not in {'__pycache__', '.pytest_cache'}:
                continue
            safe = True
            for sub, subdirs, subfiles in os.walk(candidate, followlinks=False):
                if (any(n.casefold() in PROTECTED for n in subdirs)
                        or any(linked(Path(sub) / n) for n in subdirs + subfiles)):
                    safe = False
                    break
            if safe:
                shutil.rmtree(candidate)
                dirs.remove(name)
                removed += 1
        for name in files:
            candidate = path / name
            if name.endswith('.pyc') and not linked(candidate):
                candidate.unlink()
                removed += 1
    return removed


def local_network_status(previous_report):
    probes = []
    for port in (18081, 18082, 19000):
        row = {'port': port, 'scope': 'loopback_only', 'status': 'AVAILABLE'}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(('127.0.0.1', port))
        except OSError as exc:
            row.update(status='UNAVAILABLE', errno=exc.errno, winerror=getattr(exc, 'winerror', None))
        probes.append(row)
    numbers = sorted({int(number) for number in re.findall(
        r'(?i)\b(?:WinError|Errno)\s*(?:[:=]\s*)?(\d{1,6})\b', previous_report)})
    return {'status': 'PROBED', 'loopback_bind_probes': probes,
            'previous_error_numbers': numbers,
            'scope': 'Temporary bind-and-close only; no service ownership inference or process scan'}


def core_diagnostic_fields(value, *, nested=False):
    """Select bounded core state/error metadata; never copy free-form values."""
    if not isinstance(value, dict):
        return {}
    selected = {}
    enums = {'status': {'PASS', 'FAIL', 'BLOCKED'}, 'stage': {'M1'},
             'runtime': {'unconfigured', 'idle', 'busy'}, 'storage': {'ready'},
             'runtime_state':{'STOPPED','STARTING','LOADING','READY','BUSY','STOPPING','ERROR'}}
    for key, options in enums.items():
        if isinstance(value.get(key), str) and value[key] in options:
            selected[key] = value[key]
    code = value.get('code')
    if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
        selected['code'] = code
    for key in ('errno', 'winerror', 'exit_code', 'runtime_pid', 'recovered_generations'):
        number = value.get(key)
        if type(number) is int and (-2**31 if key=='exit_code' else 0) <= number < 2**32:
            selected[key] = number
    if value.get('runtime_pid','absent') is None: selected['runtime_pid']=None
    if type(value.get('model_ready')) is bool: selected['model_ready']=value['model_ready']
    for key in ('model_load_seconds','uptime_seconds'):
        number=value.get(key)
        if type(number) in (int,float) and 0<=number<1e12: selected[key]=number
    if isinstance(value.get('generation_metrics'),dict):
        metrics={}
        for key in ('tokens_per_second','prompt_tokens','completion_tokens','generation_seconds'):
            number=value['generation_metrics'].get(key)
            if type(number) in (int,float) and 0<=number<1e12: metrics[key]=number
        if metrics: selected['generation_metrics']=metrics
    if 'active_generation' in value:
        active = value['active_generation']
        if active is None or (isinstance(active, str) and re.fullmatch(
                r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', active)):
            selected['active_generation'] = active
    error = value.get('last_runtime_error')
    if error is None and 'last_runtime_error' in value:
        selected['last_runtime_error'] = None
    elif isinstance(error, dict):
        safe = core_diagnostic_fields({key: error[key] for key in ('code', 'errno', 'winerror','exit_code')
                                      if key in error}, nested=True)
        if safe:
            selected['last_runtime_error'] = safe
    if not nested:
        for key in ('core', 'corestatus', 'core_status'):
            safe = core_diagnostic_fields(value.get(key), nested=True)
            if safe:
                selected[key] = safe
    return selected


def read_core_diagnostic_report(path):
    invalid = {'status': 'BLOCKED', 'code': 'CORE_DIAGNOSTIC_REPORT_INVALID'}
    try:
        with path.open('rb') as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            return invalid
        value = json.loads(raw)
        return core_diagnostic_fields(value) if isinstance(value, dict) else invalid
    except (OSError, ValueError, UnicodeError):
        return invalid


def diagnostics(emit):
    logs = safe_logs()
    evidence = {}
    # Only owned report filenames; never copy arbitrary logs, .env, configs or user files.
    for name in ('latest-test-report.txt', 'latest-benchmark-report.json', 'latest-core-report.json', 'latest-core-api-error.json'):
        path = logs / name
        if path.is_file() and not linked(path):
            if name in {'latest-core-report.json', 'latest-core-api-error.json'}:
                evidence[name] = read_core_diagnostic_report(path)
                continue
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 8192))
                evidence[name] = redact(stream.read(8192).decode('utf-8', errors='replace'))
    tails = {}
    owned = re.compile(r'^\d{8}-\d{6}-\d{6}-(?:all|runtime|security|core|setup|environment|benchmark|diagnostics|logs|clean)\.log$')
    candidates = sorted((p for p in logs.iterdir() if owned.fullmatch(p.name) and p.is_file() and not linked(p)), reverse=True)[:3]
    for path in candidates:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 4096))
            tail = stream.read(4096).decode('utf-8', errors='replace')
        # Drop arbitrary/raw lines: only structured toolkit report fields are eligible.
        prefixes = ('Status:', 'EGO:', 'Windows:', 'Python:', 'Suite:', 'Passed:', 'Failed:',
                    'Skipped:', 'Duration:', 'SQLCipher:', 'CRITICAL:', 'FAIL:', 'PASS:', 'BLOCKED:',
                    'Stage:', 'Action:', 'Exit code:', 'Reason:', 'M1 native Windows acceptance:')
        tails[path.name] = redact('\n'.join(line for line in tail.splitlines() if line.startswith(prefixes)))
    host = platform.system()
    report = {'environment': environment(), 'reports': evidence, 'owned_log_tails': tails,
              'local_network': local_network_status(evidence.get('latest-test-report.txt', '')),
              'current_host': host if host in {'Windows', 'Linux', 'Darwin'} else 'OTHER',
              'm1_native_windows_acceptance': 'PENDING',
              'native_windows_execution': 'NOT_TESTED'}
    text = json.dumps(report, indent=2, ensure_ascii=True)
    save(logs / 'latest-diagnostics.json', text + '\n')
    emit(text)
    return 0


def benchmark(args, emit):
    emit('Fixture checks validate runtime contracts, not model performance.')
    if not args.non_interactive and sys.stdin.isatty() and not args.fixture_check:
        mode = input('Type FIXTURE for contract checks or MODEL for local model performance (default MODEL): ').strip().upper()
        args.fixture_check = mode == 'FIXTURE'
    if args.fixture_check:
        emit('Running fixture benchmark contract checks only; real model performance: NOT_TESTED.')
        code = run_tests(argparse.Namespace(suite='runtime', test_path=['tests/server/test_benchmark.py']), emit)
        report = {'status': 'PASS' if code == 0 else 'FAIL', 'scope': 'fixture_contract_checks_only',
                  'real_model_performance': 'NOT_TESTED', 'downloads': False}
        encoded = json.dumps(report, indent=2)
        save(safe_logs() / 'latest-benchmark-report.json', encoded + '\n')
        emit(encoded)
        return code
    if not args.non_interactive and sys.stdin.isatty():
        if args.model is None:
            raw = input('Local GGUF model path (blank = BLOCKED): ').strip().strip('"')
            args.model = Path(raw) if raw else None
        if args.binary is None:
            raw = input('Local llama-server binary path (blank = BLOCKED): ').strip().strip('"')
            args.binary = Path(raw) if raw else None
    if args.model is None or not args.model.is_file():
        report = {'status': 'BLOCKED', 'reason': 'MODEL_NOT_PROVIDED', 'downloads': False}
    elif args.binary is None or not args.binary.is_file():
        report = {'status': 'BLOCKED', 'reason': 'RUNTIME_NOT_PROVIDED', 'downloads': False}
    else:
        sys.path.insert(0, str(ROOT))
        try:
            from runtime.benchmarks.run import benchmark as measure
            report = measure(args.binary.resolve(), args.model.resolve())
        except Exception as exc:
            report = {'status': 'FAIL', 'reason': 'BENCHMARK_' + type(exc).__name__}
    # Runtime report is structured synthetic benchmark output; no paths or prompts copied.
    encoded = redact(json.dumps(report, indent=2, ensure_ascii=True))
    save(safe_logs() / 'latest-benchmark-report.json', encoded + '\n')
    emit(encoded)
    return 0 if report.get('status') == 'PASS' else 2


def core_control(args, emit):
    """Delegate interactive control; only allowlisted parent metadata enters this log."""
    emit('Stage: CORE_CONTROL')
    emit('Action: ' + args.action)
    emit('M1 native Windows acceptance: PENDING')
    host = platform.system()
    emit('Control subprocess host: ' + (host if host in {'Windows', 'Linux', 'Darwin'} else 'OTHER'))
    try:
        # Inherit all streams: control owns its prompts and safe reports. Capturing
        # or forwarding arbitrary child text into toolkit logs could expose secrets.
        child = subprocess.run([sys.executable, '-m', 'server.ego_core.control',
                                args.action, '--root', str(ROOT)], cwd=ROOT)
        code = child.returncode
    except OSError as exc:
        emit('Reason: CORE_CONTROL_SPAWN_FAILED')
        for name in ('errno', 'winerror'):
            number = getattr(exc, name, None)
            if isinstance(number, int):
                emit(('WinError' if name == 'winerror' else 'Errno') + ': ' + str(number))
        code = 2
    except KeyboardInterrupt:
        emit('Reason: CORE_CONTROL_INTERRUPTED')
        code = 130
    emit('Status: ' + ('PASS' if code == 0 else 'FAIL'))
    emit('Exit code: ' + str(code))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('setup', 'environment', 'diagnostics', 'logs', 'binding-state'):
        sub.add_parser(name)
    test = sub.add_parser('test')
    test.add_argument('--suite', choices=('all', 'runtime', 'security', 'core'), default='all')
    test.add_argument('--test-path', action='append')
    bench = sub.add_parser('benchmark')
    bench.add_argument('--model', type=Path)
    bench.add_argument('--binary', type=Path)
    bench.add_argument('--non-interactive', action='store_true')
    bench.add_argument('--fixture-check', action='store_true', help='Run only fixture benchmark contract tests; not model performance')
    clean = sub.add_parser('clean')
    clean.add_argument('--yes', action='store_true', help='Confirm allowlisted project cache deletion')
    core = sub.add_parser('core')
    core.add_argument('--action', choices=('setup', 'start', 'status', 'stop', 'smoke', 'configure'), required=True)
    args = parser.parse_args(argv)
    if args.command == 'binding-state':
        print(json.dumps(binding_state()))
        return 0
    logs = safe_logs()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    log = logs / (stamp + '-' + (args.suite if args.command == 'test' else args.command) + '.log')
    def emit(message):
        text = redact(message)
        print(text, flush=True)
        with log.open('a', encoding='utf-8') as stream:
            stream.write(text + '\n')
    emit('EGO Windows Developer Toolkit; native BAT/cmd execution: NOT_TESTED')
    try:
        if args.command == 'setup':
            return setup(emit)
        if args.command == 'test':
            return run_tests(args, emit)
        if args.command == 'core':
            return core_control(args, emit)
        if args.command == 'environment':
            report = environment()
            emit(json.dumps(report, indent=2, ensure_ascii=True))
            return 0 if report['sqlcipher']['status'] == 'PASS' and report['dependency_status'] == 'PASS' else 2
        if args.command == 'benchmark':
            return benchmark(args, emit)
        if args.command == 'diagnostics':
            return diagnostics(emit)
        if args.command == 'logs':
            if os.name == 'nt':
                os.startfile(str(logs))
                emit('PASS: Opened project logs folder.')
                return 0
            emit('BLOCKED: Explorer requires Windows.')
            return 2
        if args.command == 'clean':
            confirmed = args.yes
            if not confirmed and sys.stdin.isatty():
                confirmed = input('Remove only project Python/pytest caches? Type CLEAN: ') == 'CLEAN'
            count = clean_cache(ROOT, confirmed=confirmed)
            emit(f'Cache entries removed: {count}; ' + ('PASS' if confirmed else 'CANCELLED: confirmation required'))
            return 0 if confirmed else 2
    except Exception as exc:
        # Do not print exception messages: dependencies can include credentials or private paths.
        emit('FAIL: TOOLKIT_' + type(exc).__name__)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
