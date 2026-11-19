"""Run synthetic crypto acceptance tests; never ingest user files/secrets."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def main():
    if importlib.util.find_spec('sqlcipher3') is None:
        print(json.dumps({'status': 'BLOCKED', 'reason': 'SQLCIPHER_UNAVAILABLE'}))
        return 2
    root = Path(__file__).resolve().parents[2]
    artifacts = root / 'local-artifacts'
    artifacts.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='crypto-probe-', dir=artifacts))
    result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/acceptance/test_crypto.py',
                             '-q', '--basetemp', str(work / 'synthetic-data'),
                             '--junitxml', str(work / 'tests.xml')],
                            cwd=root, capture_output=True, text=True, timeout=90)
    (work / 'result.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
    counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    if (work / 'tests.xml').exists():
        for suite in ET.parse(work / 'tests.xml').getroot().iter('testsuite'):
            for name in counts:
                counts[name] += int(suite.get(name, 0))
    report = {'status': 'PASS' if result.returncode == 0 and not counts['errors'] else 'FAIL',
              'scope': 'Linux/Windows local synthetic SQLCipher + AEAD + separate-process restore only',
              'counts': counts, 'artifact_folder': work.name,
              'recovery_secret': 'ephemeral; never exported',
              'windows_dpapi': 'PASS' if os.name == 'nt' and result.returncode == 0 else 'BLOCKED',
              'cross_windows_profile_restore': 'NOT_TESTED',
              'production_storage_encryption': 'NOT_TESTED'}
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
