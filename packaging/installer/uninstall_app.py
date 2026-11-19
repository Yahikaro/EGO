"""Uninstall.exe: removes EGO from the folder it sits in. Standard library only (a small exe).

Asks whether to keep your data (stories, characters, settings). Stops a running Core first.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MB_YESNOCANCEL, MB_ICONQUESTION, MB_ICONINFORMATION, MB_OK = 0x3, 0x20, 0x40, 0x0
IDYES, IDNO = 6, 7
CREATE_NO_WINDOW = 0x08000000
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\EGO'


def russian() -> bool:
    try:
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff) == 0x19
    except (AttributeError, OSError):
        return False


RU = russian()


def t(ru, en):
    return ru if RU else en


def box(text, flags):
    return ctypes.windll.user32.MessageBoxW(0, text, t('Удаление EGO', 'Uninstall EGO'), flags)


def shortcuts():
    appdata = os.environ.get('APPDATA') or ''
    profile = os.environ.get('USERPROFILE') or ''
    return [Path(appdata) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'EGO',
            Path(profile) / 'Desktop' / 'EGO.lnk']


def forget_registry():
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except (ImportError, OSError):
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    quiet = '--silent' in argv           # for scripts: --silent keeps your data, --silent --remove-data removes it too
    root = Path(sys.executable).resolve().parent
    if not (root / 'EGO.exe').exists():
        box(t('Рядом нет EGO.exe: удалять нечего.', 'EGO.exe is not next to this file: nothing to remove.'), MB_OK | MB_ICONINFORMATION) if not quiet else None
        return 1
    answer = (IDYES if '--remove-data' in argv else IDNO) if quiet else box(t('Удалить EGO?\n\nДа — удалить программу вместе с вашими данными (истории, персонажи, настройки, модели в папке models).\n'
                   'Нет — удалить только программу, данные оставить.\nОтмена — ничего не делать.',
                   'Remove EGO?\n\nYes — remove the program together with your data (stories, characters, settings, models in the models folder).\n'
                   'No — remove only the program and keep your data.\nCancel — do nothing.'), MB_YESNOCANCEL | MB_ICONQUESTION)
    if answer not in (IDYES, IDNO):
        return 0
    keep_data = answer == IDNO
    core = root / 'EGO-Core.exe'
    if core.exists():                                            # a running Core would lock its files
        try:
            subprocess.run([str(core), 'core', 'stop', '--root', str(root)], capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    for item in shortcuts():
        try:
            shutil.rmtree(item) if item.is_dir() else item.unlink(missing_ok=True)
        except OSError:
            pass
    forget_registry()
    keep = {'data', 'settings', 'models'} if keep_data else set()
    # This exe is running from the folder, so the last deletion is done by a helper after it exits.
    script = Path(tempfile.gettempdir()) / 'ego-uninstall.cmd'
    lines = ['@echo off', 'ping -n 3 127.0.0.1 >nul']
    for entry in root.iterdir():
        if entry.name in keep:
            continue
        lines.append(f'rmdir /s /q "{entry}" 2>nul' if entry.is_dir() else f'del /f /q "{entry}" 2>nul')
    if not keep_data:
        lines.append(f'rmdir /s /q "{root}" 2>nul')
    lines.append('del /f /q "%~f0"')
    script.write_text('\r\n'.join(lines) + '\r\n', encoding='utf-8')
    subprocess.Popen(['cmd', '/c', str(script)], creationflags=CREATE_NO_WINDOW | 0x00000008, close_fds=True)
    if not quiet: box(t('EGO удаляется. Это займёт несколько секунд.', 'EGO is being removed. This takes a few seconds.'), MB_OK | MB_ICONINFORMATION)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
