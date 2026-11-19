"""Stage 4e: automatic rotation of timestamped logs."""
import os
import time

from server.ego_core import logrotate


def _log(directory, day, index, kind, age_days=0):
    path = directory / f'202611{day:02d}-1200{index:02d}-000000-{kind}'
    path.write_text('x', encoding='utf-8')
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_keeps_newest_per_kind_and_drops_old_files(tmp_path):
    logs = tmp_path / 'logs'; logs.mkdir()
    server = [_log(logs, 1, i, 'core-server.log') for i in range(40)]           # 40 fresh files
    old = [_log(logs, 2, i, 'gui-control.json', age_days=60) for i in range(8)]  # 8 two-month-old files
    removed = logrotate.rotate(tmp_path)
    kept_server = sorted(p.name for p in logs.glob('*core-server.log'))
    assert len(kept_server) == 30 and kept_server == sorted(p.name for p in server)[-30:]
    kept_old = sorted(p.name for p in logs.glob('*gui-control.json'))
    assert len(kept_old) == 5 and kept_old == sorted(p.name for p in old)[-5:]     # always keep the newest 5
    assert removed == 10 + 3


def test_never_touches_reports_rotated_logs_or_foreign_files(tmp_path):
    logs = tmp_path / 'logs'; logs.mkdir()
    keep = ['latest-core-report.json', 'core-api-errors.log', 'core-api-errors.log.1', 'gui-bootstrap.log',
            'gui-bootstrap.log.1', 'my-notes.txt', '20261101-120000-000000-unknown.log']
    for name in keep:
        (logs / name).write_text('x', encoding='utf-8')
        os.utime(logs / name, (0, 0))                                            # very old
    for i in range(35):
        _log(logs, 3, i, 'core-control.log', age_days=90)
    logrotate.rotate(tmp_path)
    for name in keep:
        assert (logs / name).exists(), name
    assert len(list(logs.glob('*core-control.log'))) == 5


def test_missing_logs_folder_is_fine(tmp_path):
    assert logrotate.rotate(tmp_path) == 0


def test_core_start_rotates_before_spawning(tmp_path, monkeypatch):
    from server.ego_core import control
    called = []
    monkeypatch.setattr(control.KeyVault, 'unlock', lambda self: None)
    monkeypatch.setattr(logrotate, 'rotate', lambda root: called.append(root) or 0)
    monkeypatch.setattr('server.ego_core.preflight.run',
                        lambda root, **k: {'ok': False, 'errors': [{'code': 'CONFIG_INVALID'}], 'warnings': []})
    try:
        control.start(tmp_path)
    except control.ControlFailure:
        pass
    assert called == [tmp_path]
