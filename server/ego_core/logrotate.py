"""Automatic rotation of EGO's timestamped log files (stage 4e).

Every Core start, BAT action and GUI action writes its own `<stamp>-<kind>.log|json`
file under logs/. Without rotation they accumulate forever. Policy, per kind:
  * keep at most KEEP_PER_KIND newest files;
  * delete files older than MAX_AGE_DAYS;
  * but always keep the KEEP_MIN newest, so diagnostics survive a long break.
Never touched: latest-* reports, size-rotated logs (core-api-errors.log*, gui-bootstrap.log*),
anything not matching the EGO pattern, symlinks, files outside logs/.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path

STAMP = r'\d{8}-\d{6}-\d{6}'
KINDS = (r'(?:all|runtime|security|core|setup|environment|benchmark|diagnostics|logs|clean|core-control|core-server)\.log'
         r'|gui-control\.json')
OWNED = re.compile(r'^' + STAMP + r'-(?:' + KINDS + r')$')
KEEP_PER_KIND = 30
KEEP_MIN = 5
MAX_AGE_DAYS = 30


def _kind(name):
    return name[len('00000000-000000-000000-'):]


def rotate(root, *, keep=KEEP_PER_KIND, keep_min=KEEP_MIN, max_age_days=MAX_AGE_DAYS, now=None):
    """Delete surplus/old timestamped EGO logs. Returns the number of files removed."""
    directory = Path(root) / 'logs'
    if directory.is_symlink() or not directory.is_dir():
        return 0
    groups = defaultdict(list)
    for path in directory.iterdir():
        if OWNED.fullmatch(path.name) and not path.is_symlink() and path.is_file():
            groups[_kind(path.name)].append(path)
    cutoff = (time.time() if now is None else now) - max_age_days * 86400
    removed = 0
    for files in groups.values():
        files.sort(key=lambda p: p.name, reverse=True)       # names start with a UTC timestamp
        for index, path in enumerate(files):
            if index < keep_min:
                continue
            try:
                if index >= keep or path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue                                      # in use or already gone: try next time
    return removed
