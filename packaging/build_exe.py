"""Build the packaged EGO (PyInstaller onedir) and optionally install it next to existing data.

  .venv\\Scripts\\python.exe -m pip install -r packaging\\requirements-build.txt
  .venv\\Scripts\\python.exe packaging\\build_exe.py                 # -> dist\\EGO\\ (clean package)
  .venv\\Scripts\\python.exe packaging\\build_exe.py --install       # also copy EGO.exe, EGO-Core.exe, _internal\\ here

The package never contains data\\, settings\\, logs\\ or .venv. A client copy is
placed in dist\\EGO\\client\\ so Core can serve /app/. --install copies only the
executables and _internal\\ into the project folder (Core must be stopped); the
existing data\\ there is used as is and is never modified by the build.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist' / 'EGO'
FORBIDDEN = ('data', 'settings', 'logs', '.venv')
INSTALLED = ('EGO.exe', 'EGO-Core.exe', '_internal')


def build():
    subprocess.run([sys.executable, '-m', 'PyInstaller', str(ROOT / 'packaging' / 'EGO.spec'), '--noconfirm', '--clean',
                    '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build')], cwd=ROOT, check=True)
    client = ROOT / 'client'
    if (client / 'index.html').is_file():
        shutil.copytree(client, DIST / 'client', ignore=shutil.ignore_patterns('tests', '*.bat'), dirs_exist_ok=True)
    verify(DIST)


def verify(package):
    for path in package.rglob('*'):
        relative = path.relative_to(package).parts
        # EGO user data: encrypted stores, DPAPI vault and TLS identity (public CA bundles are fine).
        if (relative[0] in FORBIDDEN or '.venv' in relative or path.suffix in ('.db', '.db-wal', '.db-shm')
                or path.name in ('keys.protected', 'identity.key.pem', 'identity.crt.pem', 'core.lock',
                                 'lan.key.pem', 'lan.crt.pem', 'lan-status.json', 'mail.json', 'network.json')):
            raise SystemExit(f'package contains forbidden user data or environment: {path}')
    for name in ('EGO.exe', 'EGO-Core.exe', '_internal'):
        if not (package / name).exists():
            raise SystemExit(f'package incomplete: {name} missing')
    size = sum(p.stat().st_size for p in package.rglob('*') if p.is_file())
    print(f'package OK: {package} ({size / 1024**2:.0f} MB), no data/settings/logs/.venv')


def install():
    sys.path.insert(0, str(ROOT))
    from server.ego_core import control
    if (ROOT / 'data').is_dir() and not control._lease_available(ROOT):
        raise SystemExit('Core of this installation is running: stop it before installing EGO.exe')
    for name in INSTALLED:
        target = ROOT / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        source = DIST / name
        (shutil.copytree if source.is_dir() else shutil.copy2)(source, target)
    print(f'installed {", ".join(INSTALLED)} into {ROOT}; data\\ left untouched')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='copy the executables next to this project data')
    parser.add_argument('--skip-build', action='store_true')
    args = parser.parse_args()
    if not args.skip_build:
        build()
    else:
        verify(DIST)
    if args.install:
        install()


if __name__ == '__main__':
    main()
