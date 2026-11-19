"""Build the Windows setup program  dist\\EGO-Setup.exe.

  .venv\\Scripts\\python.exe packaging\\build_installer.py              # builds the program too (build_exe.py)
  .venv\\Scripts\\python.exe packaging\\build_installer.py --skip-build # reuse dist\\EGO

Steps: EGO.ico from the client icon -> Uninstall.exe (stdlib only) -> payload.zip (dist\\EGO + Uninstall.exe + icon
+ models\\ note + scripts\\install_tts.py) -> EGO-Setup.exe (PyInstaller onefile, payload inside).
"""
from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
PACKAGE = DIST / 'EGO'
WORK = ROOT / 'build' / 'installer'
SRC = ROOT / 'packaging' / 'installer'
MODELS_NOTE = ('Put your model files (.gguf) into this folder.\r\n'
               'Положите файлы моделей (.gguf) в эту папку.\r\n\r\n'
               'EGO finds them here. Where to get models: see the project README.\r\n')


def make_icon(target: Path) -> Path:
    """EGO.ico (one 256x256 PNG inside) from the client icon, scaled with Qt."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage
    image = QImage(str(ROOT / 'client' / 'assets' / 'icons' / 'icon-512.png'))
    if image.isNull():
        raise SystemExit('client icon not found')
    image = image.scaled(256, 256, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    png = bytes(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', 0, 0, 0, 0, 1, 32, len(png), 22) + png)
    return target


def pyinstaller(*args):
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN', *args], cwd=ROOT, check=True)


def build_uninstaller(icon: Path) -> Path:
    out = WORK / 'uninstall'
    pyinstaller('--onefile', '--windowed', '--name', 'Uninstall', '--icon', str(icon), '--version-file', str(ROOT / 'packaging' / 'version_info.txt'), '--distpath', str(out / 'dist'),
                '--workpath', str(out / 'work'), '--specpath', str(out), str(SRC / 'uninstall_app.py'))
    return out / 'dist' / 'Uninstall.exe'


def build_payload(icon: Path, uninstaller: Path) -> Path:
    staging = WORK / 'payload'
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(PACKAGE, staging)
    shutil.copy2(icon, staging / 'EGO.ico')
    shutil.copy2(uninstaller, staging / 'Uninstall.exe')
    (staging / 'models').mkdir()
    (staging / 'models' / 'PUT_MODELS_HERE.txt').write_text(MODELS_NOTE, encoding='utf-8')
    (staging / 'scripts').mkdir()
    shutil.copy2(ROOT / 'scripts' / 'install_tts.py', staging / 'scripts' / 'install_tts.py')
    for name in ('README.md', 'THIRD_PARTY.md', 'LICENSE'):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, staging / name)
    payload = WORK / 'payload.zip'
    payload.unlink(missing_ok=True)
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(staging.rglob('*')):
            if path.is_file():
                z.write(path, path.relative_to(staging).as_posix())
    print(f'payload: {payload.stat().st_size / 1024 ** 2:.0f} MB')
    return payload


def build_setup(icon: Path, payload: Path) -> Path:
    out = WORK / 'setup'
    sep = ';'
    pyinstaller('--onefile', '--windowed', '--name', 'EGO-Setup', '--icon', str(icon), '--version-file', str(ROOT / 'packaging' / 'version_info.txt'), '--distpath', str(DIST),
                '--workpath', str(out / 'work'), '--specpath', str(out),
                '--add-data', f'{payload}{sep}.', '--add-data', f'{icon}{sep}.',
                '--paths', str(ROOT), '--paths', str(SRC),
                '--exclude-module', 'server', '--exclude-module', 'tests', '--exclude-module', 'pytest',
                str(SRC / 'setup_app.py'))
    return DIST / 'EGO-Setup.exe'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-build', action='store_true', help='reuse dist\\EGO instead of rebuilding the program')
    args = parser.parse_args()
    if not args.skip_build:
        subprocess.run([sys.executable, str(ROOT / 'packaging' / 'build_exe.py')], cwd=ROOT, check=True)
    elif not (PACKAGE / 'EGO.exe').exists():
        raise SystemExit('dist\\EGO is missing: build without --skip-build')
    icon = make_icon(WORK / 'EGO.ico')
    setup = build_setup(icon, build_payload(icon, build_uninstaller(icon)))
    print(f'installer: {setup} ({setup.stat().st_size / 1024 ** 2:.0f} MB)')


if __name__ == '__main__':
    main()
