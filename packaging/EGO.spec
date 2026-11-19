# PyInstaller spec for EGO (onedir). Build with:  .venv\Scripts\python.exe packaging\build_exe.py
# Two executables share one _internal\ folder:
#   EGO.exe       windowed Control Center
#   EGO-Core.exe  console: hidden Core daemon, control CLI, force-close supervisor
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)  # project folder; this spec lives in packaging\\


def ours(package):
    # Application code only; tests import pytest and are never shipped.
    return [name for name in collect_submodules(package) if '.tests' not in name and not name.endswith('tests')]


hidden = (ours('server') + ours('runtime') + ours('windows_ui') + ours('scripts')
          + collect_submodules('uvicorn') + ['sqlcipher3', 'sqlcipher3.dbapi2'])

analysis = Analysis(
    [os.path.join(SPECPATH, 'ego_launcher.py')],
    pathex=[ROOT],
    hiddenimports=hidden,
    excludes=['pytest', '_pytest', 'pluggy', 'tkinter', 'unittest.mock', 'PyInstaller'],
    datas=[(os.path.join(ROOT, 'server', 'ego_tts', 'worker.py'), os.path.join('server', 'ego_tts'))],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

gui = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='EGO', console=False,
          disable_windowed_traceback=True, upx=False, version=os.path.join(SPECPATH, 'version_info.txt'))
core = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='EGO-Core', console=True, upx=False, version=os.path.join(SPECPATH, 'version_info.txt'))

COLLECT(gui, core, analysis.binaries, analysis.datas, name='EGO', upx=False)
