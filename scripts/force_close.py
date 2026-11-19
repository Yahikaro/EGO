"""Explicit, root-scoped emergency shutdown for an EGO installation.

No pid file is trusted; process identity is checked before each signal.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys
import time


def _packaged(args, root, exe_name):
    """Packaged EGO: the exact exe of this installation (root/<exe_name>)."""
    try:
        return bool(args) and Path(args[0]).resolve() == (root / exe_name).resolve()
    except (OSError, ValueError, TypeError):
        return False


def process_matches_core(cmdline, root):
    """Require an exact module and root argument; never use a substring search."""
    try:
        args = list(cmdline)
        if _packaged(args, root, 'EGO-Core.exe') and args[1:2] == ['daemon']:
            pass  # EGO-Core.exe daemon --root <root>
        else:
            for i in range(len(args)-1):
                if args[i] == '-m' and args[i+1] == 'server.ego_core.daemon':
                    break
            else:
                return False
        for i in range(len(args)-1):
            if args[i] == '--root' and Path(args[i+1]).resolve() == root.resolve():
                return True
    except (OSError, ValueError, TypeError):
        pass
    return False


def process_matches_gui(cmdline, root):
    try:
        args = list(cmdline)
        if _packaged(args, root, 'EGO.exe'):
            return True  # the Control Center window of this installation
        return any(args[i:i+2] == ['-m','windows_ui'] for i in range(len(args)-1)) and any(
            args[i] == '--root' and Path(args[i+1]).resolve() == root.resolve()
            for i in range(len(args)-1))
    except (OSError, ValueError, TypeError):
        return False


def snapshot_owned(root, *, gui=False):
    import psutil
    found=[]
    for proc in psutil.process_iter(['pid','cmdline','create_time']):
        try:
            if process_matches_core(proc.info['cmdline'],root) or (gui and process_matches_gui(proc.info['cmdline'],root)):
                found.append(proc)
        except (psutil.Error, OSError, ValueError):
            continue
    return found


def is_same(proc, pid, created):
    import psutil
    try:
        return proc.pid == pid and abs(proc.create_time()-created) < 0.001 and proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, OSError):
        return False


def emergency_shutdown(root, *, gui=False, grace=4.0):
    import psutil
    root=Path(root).resolve()
    owners=snapshot_owned(root,gui=gui)
    # Snapshot children before asking the owner to exit; descendants are only
    # eligible when their parent relationship was witnessed, never by name.
    owned={}
    for owner in owners:
        try:
            for proc in owner.children(recursive=True):
                owned[proc.pid]=(proc,proc.create_time(),'child')
            owned[owner.pid]=(owner,owner.create_time(),'gui' if process_matches_gui(owner.cmdline(),root) else 'core')
        except (psutil.Error, OSError):
            continue
    if any(kind=='core' for _,_,kind in owned.values()):
        try:
            from server.ego_core import control
            # Request asynchronously: the caller remains bounded even if an
            # authenticated Core request hangs or its shutdown endpoint stalls.
            import threading
            t=threading.Thread(target=lambda: _try_stop(control,root),daemon=True)
            t.start()
        except Exception:
            pass
    deadline=time.monotonic()+grace
    while time.monotonic()<deadline:
        if not any(is_same(proc,pid,created) for pid,(proc,created,_) in owned.items()):
            break
        time.sleep(.1)
    # Children first. Termination of Windows job-owning Core also kills the
    # Runtime. No unrelated process is ever killed by executable name.
    failed=[]
    for pid,(proc,created,kind) in sorted(owned.items(),key=lambda row:row[1][2]!='child'):
        if not is_same(proc,pid,created): continue
        try: proc.terminate()
        except (psutil.NoSuchProcess,psutil.AccessDenied) as exc:
            if isinstance(exc,psutil.AccessDenied): failed.append(pid)
    _,alive=psutil.wait_procs([p for pid,(p,created,_) in owned.items() if is_same(p,pid,created)],timeout=2)
    for proc in alive:
        record=owned.get(proc.pid)
        if record and is_same(proc,proc.pid,record[1]):
            try: proc.kill()
            except (psutil.NoSuchProcess,psutil.AccessDenied) as exc:
                if isinstance(exc,psutil.AccessDenied): failed.append(proc.pid)
    psutil.wait_procs([p for pid,(p,created,_) in owned.items() if is_same(p,pid,created)],timeout=3)
    # Detect remaining matching owners as well as children (no success lie).
    remaining=[pid for pid,(p,created,_) in owned.items() if is_same(p,pid,created)]
    remaining.extend(p.pid for p in snapshot_owned(root,gui=gui) if p.pid not in owned)
    return sorted(set(remaining+failed))


def _try_stop(control,root):
    try: control.request(root,'POST','/v1/system/shutdown',payload={},timeout=2)
    except Exception: pass


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--all-guis',action='store_true')
    args=parser.parse_args(argv)
    if not args.root.is_dir():
        print('EGO_ROOT_NOT_FOUND'); return 2
    try: remaining=emergency_shutdown(args.root,gui=args.all_guis)
    except ImportError:
        print('PSUTIL_REQUIRED'); return 3
    if remaining:
        print('EGO_FORCE_STOP_INCOMPLETE pid='+','.join(map(str,remaining))); return 1
    print('EGO_FORCE_STOP_COMPLETE'); return 0

if __name__=='__main__':
    raise SystemExit(main())
