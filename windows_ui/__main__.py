"""Native Qt Control Center with a per-user, per-project lifetime lease."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
        else Path(__file__).resolve().parents[1])


def instance_identifier(root: Path) -> str:
    identity = str(Path.home().resolve()) + '\0' + getpass.getuser() + '\0' + os.path.normcase(str(root.resolve()))
    return 'ego-control-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:40]


def load_handoff(ready: Path | None, error: Path | None) -> tuple[Path, Path, str] | None:
    """Only fresh files in a private bootstrap-created temporary folder."""
    if ready is None and error is None:
        return None
    if ready is None or error is None:
        raise ValueError('INVALID_HANDOFF')
    parent = ready.parent
    if (ready.name != 'ready.json' or error.name != 'error.json' or error.parent != parent
            or not parent.name.startswith('ego-gui-bootstrap-')
            or parent.parent.resolve() != Path(tempfile.gettempdir()).resolve()
            or parent.is_symlink() or parent.resolve() != parent.parent.resolve() / parent.name
            or ready.exists() or ready.is_symlink() or error.exists() or error.is_symlink()):
        raise ValueError('INVALID_HANDOFF')
    if os.name != 'nt' and (parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077):
        raise ValueError('INVALID_HANDOFF')
    request = parent/'request.json'
    if request.is_symlink() or request.stat().st_size > 4096:
        raise ValueError('INVALID_HANDOFF')
    token = json.loads(request.read_text(encoding='utf-8')).get('token')
    if not isinstance(token, str) or not 16 <= len(token) <= 128:
        raise ValueError('INVALID_HANDOFF')
    return ready, error, token


def write_record(handoff, *, error: bool = False, mode: str = 'window', code: int = 31, reason: str = 'GUI_STARTUP_FAILED') -> None:
    if handoff is None:
        return
    path = handoff[1 if error else 0]
    # On Windows a venv's Scripts\pythonw.exe is a launcher that runs the real
    # interpreter as a child, so the bootstrap only knows the launcher PID.
    record = {'pid': os.getpid(), 'parent_pid': os.getppid(), 'token': handoff[2],
              'status': 'error' if error else 'ready'}
    if error:
        record.update(code=code, reason=reason)
    else:
        record['mode'] = mode
    # Atomic publication, exclusive destination; no existing files overwritten.
    temporary = path.with_name(path.name + '.writing')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(record, stream)
    # Handoff directory is private and the destination was checked absent.
    # os.link() is not reliable on all Windows filesystems; replace atomically.
    if path.exists():
        temporary.unlink(missing_ok=True)
        raise FileExistsError('HANDOFF_EXISTS')
    os.replace(temporary, path)


def report_failure(handoff, reason: str, code: int) -> int:
    # Exception text can contain credentials, file contents and private paths.
    try:
        write_record(handoff, error=True, reason=reason, code=code)
    except Exception:
        pass
    if sys.stderr is not None:
        print(f'EGO {reason} ({code})', file=sys.stderr)
    return code


def settings_directory() -> Path:
    from PySide6.QtCore import QStandardPaths
    base = Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation))
    directory = base/'EGO'/'ControlCenter'
    # All state locations are derived internally; reject symlink redirection.
    for path in (base, base/'EGO', directory):
        if path.is_symlink():
            raise ValueError('UNSAFE_SETTINGS_DIRECTORY')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        if directory.stat().st_uid != os.getuid():
            raise ValueError('UNSAFE_SETTINGS_DIRECTORY')
        directory.chmod(0o700)
    return directory


def activate_existing(name: str) -> bool:
    from PySide6.QtNetwork import QLocalSocket
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(2000):
        return False
    socket.write(b'ACTIVATE\n')
    socket.flush()
    if socket.bytesAvailable() or socket.waitForReadyRead(2000):
        return bytes(socket.readAll()) == b'ACTIVATED\n'
    return False


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default echoes unknown arguments, including accidental keys.
        raise ValueError('INVALID_BOOTSTRAP_HANDOFF')


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description='EGO Server Control Center')
    parser.add_argument('--root', type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument('--ready-file', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--error-file', type=Path, help=argparse.SUPPRESS)
    handoff = None
    try:
        args = parser.parse_args(argv)
        handoff = load_handoff(args.ready_file, args.error_file)
    except Exception:
        return report_failure(None, 'INVALID_BOOTSTRAP_HANDOFF', 30)
    try:
        from PySide6.QtCore import QLockFile, QTimer
        from PySide6.QtNetwork import QLocalServer
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(['EGO Control Center'])
        app.setQuitOnLastWindowClosed(False)
        app.setOrganizationName('EGO')
        app.setApplicationName('EGO Control Center')
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError('INVALID_PROJECT_ROOT')
        name = instance_identifier(root)
        lock_path = settings_directory()/(name+'.lock')
        if lock_path.is_symlink():
            raise ValueError('UNSAFE_SETTINGS_LOCK')
        lease = QLockFile(str(lock_path))
        lease.setStaleLockTime(0)
        if not lease.tryLock(0):
            # The owner may be constructing its window; bounded retries never
            # remove a live lock/socket or start another controller/Core.
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                if activate_existing(name):
                    write_record(handoff, mode='activated')
                    return 0
                time.sleep(.1)
            return report_failure(handoff, 'GUI_INSTANCE_ACTIVATION_FAILED', 32)
        from windows_ui.window import ControlCenterWindow
        window = ControlCenterWindow(root)
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.UserAccessOption)
        # Only the exclusive lease owner can remove a stale crashed socket.
        QLocalServer.removeServer(name)
        if not server.listen(name):
            lease.unlock()
            return report_failure(handoff, 'GUI_INSTANCE_LISTEN_FAILED', 33)
        sockets = []

        def incoming():
            while server.hasPendingConnections():
                socket = server.nextPendingConnection()
                sockets.append(socket)

                def receive(connection=socket):
                    if connection.canReadLine():
                        request = bytes(connection.readLine(64))
                        if request == b'ACTIVATE\n':
                            window.showNormal()
                            window.raise_()
                            window.activateWindow()
                            connection.write(b'ACTIVATED\n')
                            connection.flush()
                        connection.disconnectFromServer()
                socket.readyRead.connect(receive)
                socket.disconnected.connect(socket.deleteLater)
                QTimer.singleShot(2000, socket.disconnectFromServer)
                if socket.bytesAvailable():
                    receive()
        server.newConnection.connect(incoming)
        window.show()

        def ready():
            try:
                write_record(handoff)
            except Exception:
                report_failure(handoff, 'GUI_READY_WRITE_FAILED', 34)
                app.exit(34)
        QTimer.singleShot(0, ready)
        result = app.exec()
        server.close()
        lease.unlock()
        return result
    except Exception:
        return report_failure(handoff, 'GUI_STARTUP_FAILED', 31)


if __name__ == '__main__':
    raise SystemExit(main())
