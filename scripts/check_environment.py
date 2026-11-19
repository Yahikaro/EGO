"""Redacted, read-only environment inventory. Never scans personal files."""
import importlib.metadata
import json
import platform
import shutil
import subprocess


def gpu_inventory():
    if not shutil.which('nvidia-smi'):
        return {'status': 'BLOCKED', 'reason': 'NVIDIA_SMI_NOT_FOUND'}
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,memory.used',
                            '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5)
        if r.returncode:
            return {'status': 'BLOCKED', 'reason': 'NVIDIA_QUERY_FAILED'}
        return {'status': 'PASS', 'devices': r.stdout.strip().splitlines()}
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'BLOCKED', 'reason': 'NVIDIA_QUERY_FAILED'}


def inventory():
    packages = {}
    for name in ['fastapi', 'pydantic', 'httpx', 'cryptography', 'uvicorn', 'pytest', 'psutil', 'sqlcipher3-binary']:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {'os': platform.system(), 'architecture': platform.machine(),
            'python': platform.python_version(), 'packages': packages, 'gpu': gpu_inventory(),
            'windows_dpapi': 'AVAILABLE_UNTESTED' if platform.system() == 'Windows' else 'BLOCKED',
            'xcode': 'AVAILABLE_UNTESTED' if shutil.which('xcodebuild') else 'BLOCKED',
            'real_iphone': 'NOT_TESTED', 'target_windows_gpu': 'NOT_TESTED'}


if __name__ == '__main__':
    print(json.dumps(inventory(), indent=2))
