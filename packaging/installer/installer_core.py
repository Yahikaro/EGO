"""EGO setup: the logic without any window (so it can be tested and run silently).

Per-user install, no administrator rights: copy the program, make shortcuts, register the
uninstaller in Windows "Apps", and, when asked, download llama.cpp / cloudflared or install
the optional voice output.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

APP = 'EGO'
VERSION = '1.0.0'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\EGO'
LLAMA_API = 'https://api.github.com/repos/ggml-org/llama.cpp/releases/latest'
CLOUDFLARED_URL = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
USER_AGENT = 'EGO-Setup'
CREATE_NO_WINDOW = 0x08000000


def default_dir() -> Path:
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    return Path(base) / 'Programs' / APP


def payload_path() -> Path:
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'payload.zip'


def payload_size(zip_path: Path) -> int:
    with zipfile.ZipFile(zip_path) as z:
        return sum(i.file_size for i in z.infolist())


def free_bytes(path: Path) -> int:
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def check_target(path: Path) -> str | None:
    """None when the folder is fine, else a short code: EMPTY, NOT_WRITABLE, NOT_EGO_FOLDER."""
    path = Path(path)
    if not str(path).strip():
        return 'EMPTY'
    try:
        if path.exists():
            if not path.is_dir():
                return 'NOT_WRITABLE'
            entries = list(path.iterdir())
            if entries and not (path / 'EGO.exe').exists():
                return 'NOT_EGO_FOLDER'
        path.mkdir(parents=True, exist_ok=True)
        probe = path / '.ego-write-test'
        probe.write_text('x')
        probe.unlink()
    except OSError:
        return 'NOT_WRITABLE'
    return None


def extract(zip_path: Path, dest: Path, progress=lambda fraction, name='': None) -> int:
    """Unpack the program. Never touches data\\ or settings\\ of an existing installation."""
    dest = Path(dest).resolve()
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        members = [i for i in z.infolist() if (Path(i.filename).parts[:1] or ('',))[0] not in ('data', 'settings', 'logs')]
        total = max(1, sum(i.file_size for i in members))
        done = 0
        for info in members:
            target = (dest / info.filename).resolve()
            if dest not in target.parents and target != dest:
                raise ValueError('UNSAFE_PATH')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, 'wb') as out:
                shutil.copyfileobj(src, out, 1024 * 256)
            done += info.file_size
            count += 1
            progress(done / total, info.filename)
    return count


# ---- downloads -------------------------------------------------------------------------
def _open(url: str, timeout: int = 30):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=timeout)


def pick_llama_assets(release: dict) -> dict:
    """The CUDA 12 Windows x64 build of llama.cpp and its CUDA runtime from a GitHub release."""
    llama = cudart = None
    for asset in release.get('assets', []):
        name = asset.get('name', '').lower()
        url = asset.get('browser_download_url')
        if not url or not name.endswith('.zip') or 'x64' not in name or 'win' not in name:
            continue
        if name.startswith('llama-') and 'bin-win-cuda-12' in name and llama is None:
            llama = url
        elif name.startswith('cudart-') and 'cuda-12' in name and cudart is None:
            cudart = url
    return {'llama': llama, 'cudart': cudart}


def download(url: str, dest: Path, progress=lambda fraction: None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    with _open(url, 60) as response, open(tmp, 'wb') as out:
        total = int(response.headers.get('Content-Length') or 0)
        got = 0
        while True:
            block = response.read(1024 * 256)
            if not block:
                break
            out.write(block)
            got += len(block)
            if total:
                progress(got / total)
    tmp.replace(dest)
    return dest


def install_llama(root: Path, log=print, progress=lambda fraction: None) -> bool:
    """llama-server.exe + CUDA runtime into <root>\\runtime\\llama."""
    folder = Path(root) / 'runtime' / 'llama'
    try:
        with _open(LLAMA_API, 20) as response:
            assets = pick_llama_assets(json.loads(response.read().decode('utf-8')))
    except Exception as exc:                                    # network, rate limit, odd JSON
        log(f'llama.cpp: could not read the release list ({exc})')
        return False
    if not assets['llama']:
        log('llama.cpp: no matching CUDA build in the latest release')
        return False
    tmp = Path(os.environ.get('TEMP') or str(root)) / 'ego-setup-downloads'
    tmp.mkdir(parents=True, exist_ok=True)
    pieces = [u for u in (assets['llama'], assets['cudart']) if u]
    try:
        for index, url in enumerate(pieces):
            name = url.rsplit('/', 1)[-1]
            log(f'Downloading {name}')
            archive = download(url, tmp / name, lambda f, i=index: progress((i + f) / len(pieces)))
            with zipfile.ZipFile(archive) as z:
                z.extractall(folder)
            archive.unlink(missing_ok=True)
    except Exception as exc:
        log(f'llama.cpp: failed ({exc})')
        return False
    return (folder / 'llama-server.exe').exists()


def install_cloudflared(root: Path, log=print, progress=lambda fraction: None) -> bool:
    target = Path(root) / 'runtime' / 'tunnel' / 'cloudflared.exe'
    try:
        log('Downloading cloudflared.exe')
        download(CLOUDFLARED_URL, target, progress)
    except Exception as exc:
        log(f'cloudflared: failed ({exc})')
        return False
    return target.exists()


def find_python312() -> list[str] | None:
    """Command that starts Python 3.12 (needed only for the voice output)."""
    for command in (['py', '-3.12'], ['python']):
        try:
            result = subprocess.run([*command, '-c', 'import sys;print(sys.version_info[:2]==(3,12) and sys.maxsize>2**32)'],
                                    capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout.strip() == 'True':
            return command
    return None


def install_tts(root: Path, python: list[str], log=print) -> bool:
    script = Path(root) / 'scripts' / 'install_tts.py'
    if not script.exists():
        log('voice output: scripts\\install_tts.py is missing')
        return False
    process = subprocess.Popen([*python, str(script)], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace', creationflags=CREATE_NO_WINDOW)
    for line in process.stdout:
        log(line.rstrip())
    return process.wait() == 0


# ---- Windows integration -----------------------------------------------------------------------
def start_menu_dir() -> Path:
    base = os.environ.get('APPDATA') or str(Path.home() / 'AppData' / 'Roaming')
    return Path(base) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / APP


def desktop_dir() -> Path:
    return Path(os.environ.get('USERPROFILE') or str(Path.home())) / 'Desktop'


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def make_shortcut(link: Path, target: Path, arguments: str = '', workdir: Path | None = None, icon: Path | None = None,
                  description: str = '', minimized: bool = False) -> bool:
    """A .lnk through Windows Script Host (PowerShell); no extra packages."""
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    lines = ['$s=(New-Object -ComObject WScript.Shell).CreateShortcut(' + _ps_quote(str(link)) + ')',
             '$s.TargetPath=' + _ps_quote(str(target))]
    if arguments:
        lines.append('$s.Arguments=' + _ps_quote(arguments))
    if workdir:
        lines.append('$s.WorkingDirectory=' + _ps_quote(str(workdir)))
    if icon:
        lines.append('$s.IconLocation=' + _ps_quote(str(icon)))
    if description:
        lines.append('$s.Description=' + _ps_quote(description))
    if minimized:
        lines.append('$s.WindowStyle=7')
    lines.append('$s.Save()')
    try:
        result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', '; '.join(lines)],
                                capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and link.exists()


def make_shortcuts(root: Path, desktop: bool, start_menu: bool) -> list[Path]:
    root = Path(root)
    icon = root / 'EGO.ico'
    made = []
    targets = []
    if start_menu:
        folder = start_menu_dir()
        targets += [(folder / 'EGO.lnk', root / 'EGO.exe', '', False, 'EGO server window'),
                    (folder / 'EGO Client.lnk', root / 'EGO-Core.exe', f'open-client --root "{root}"', True, 'Open EGO in the browser'),
                    (folder / 'Uninstall EGO.lnk', root / 'Uninstall.exe', '', False, 'Remove EGO')]
    if desktop:
        targets.append((desktop_dir() / 'EGO.lnk', root / 'EGO.exe', '', False, 'EGO server window'))
    for link, target, args, hidden, text in targets:
        if target.exists() and make_shortcut(link, target, args, root, icon if icon.exists() else target, text, hidden):
            made.append(link)
    return made


def register_uninstaller(root: Path, size_bytes: int = 0) -> bool:
    try:
        import winreg
    except ImportError:
        return False
    root = Path(root)
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            for name, value in (('DisplayName', 'EGO'), ('DisplayVersion', VERSION), ('Publisher', 'EGO'),
                                ('InstallLocation', str(root)), ('DisplayIcon', str(root / 'EGO.ico')),
                                ('UninstallString', f'"{root / "Uninstall.exe"}"')):
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            winreg.SetValueEx(key, 'NoModify', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, 'NoRepair', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, 'EstimatedSize', 0, winreg.REG_DWORD, max(1, size_bytes // 1024))
    except OSError:
        return False
    return True


def unregister_uninstaller() -> None:
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except (ImportError, OSError):
        pass
