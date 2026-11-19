"""Entry point of the packaged EGO.exe / EGO-Core.exe (PyInstaller onedir).

  EGO.exe                         Control Center window (windowed exe)
  EGO-Core.exe core <action>      control CLI: setup | start | status | stop | smoke | configure | open-client
  EGO-Core.exe open-client        start Core if needed and open the client (same as START_EGO_CLIENT.bat)
  EGO-Core.exe daemon ...         hidden Core process started by control.start (not for manual use)
  EGO-Core.exe force-close ...    emergency shutdown supervisor started by the Control Center

Both executables share _internal\\ and resolve their data root as the folder that
holds the exe, so data\\, settings\\ and logs\\ stay outside the bundle.
Running from source keeps working exactly as before (python -m ...).
"""
from __future__ import annotations

import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else ''
    if command == 'daemon':
        from server.ego_core import daemon
        sys.argv = [sys.argv[0], *argv[1:]]
        return daemon.main()
    if command == 'core':
        from server.ego_core import control
        return control.main(argv[1:])
    if command == 'open-client':
        from server.ego_core import control
        return control.main(['open-client', *argv[1:]])
    if command == 'force-close':
        from scripts import force_close
        return force_close.main(argv[1:])
    from windows_ui.__main__ import main as gui
    return gui(argv)


if __name__ == '__main__':
    raise SystemExit(main())
