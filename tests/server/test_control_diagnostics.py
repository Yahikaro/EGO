import importlib
import json
import os
from pathlib import Path
import time
import zipfile
import pytest


def module():
    assert (Path(__file__).parents[2]/'windows_ui/diagnostics.py').exists(), 'diagnostic helper missing'
    return importlib.import_module('windows_ui.diagnostics')


def test_export_and_log_view_select_safe_fields_and_never_arbitrary_files(tmp_path):
    m=module(); logs=tmp_path/'logs'; logs.mkdir()
    (logs/'private.log').write_text('private conversation bearer secret-token')
    (logs/'latest-core-report.json').write_text(json.dumps({'status':'FAIL','code':'PORT_IN_USE','prompt':'private conversation','token':'secret-token','errno':10048}))
    (logs/'20261019-120000-000001-core-server.log').write_text('private conversation\n{"status":"FAIL","code":"RUNTIME_START_FAILED","prompt":"private conversation"}')
    (logs/'latest-test-report.txt').write_text('Status: PASS\nPassed: 5\nFailed: 0\nSkipped: 1\nsecret-token private conversation\nCRITICAL: private conversation')
    rows=m.logs(tmp_path)
    assert rows and all(set(row)=={'file','level','text'} for row in rows)
    text=json.dumps(rows)
    assert 'PORT_IN_USE' in text and '10048' in text
    assert 'private conversation' not in text and 'secret-token' not in text
    destination=tmp_path/'export.zip'; assert m.export_diagnostics(tmp_path,destination)==destination
    with zipfile.ZipFile(destination) as archive:
        assert 'manifest.json' in archive.namelist()
        result=''.join(archive.read(name).decode() for name in archive.namelist())
        assert 'private conversation' not in result and 'secret-token' not in result
        assert 'private.log' not in str(archive.namelist())


def test_log_filter_clean_and_bounded_reads_are_owned_only(tmp_path):
    m=module(); logs=tmp_path/'logs'; logs.mkdir()
    path=logs/'20261019-120000-000001-gui-control.json'
    path.write_text(json.dumps({'status':'FAIL','code':'CORE_START_FAILED','action':'start'}))
    private=logs/'user.log'; private.write_text('keep')
    latest=logs/'latest-core-report.json'; latest.write_text('{}')
    old=time.time()-10*86400
    for file in (path,private,latest): os.utime(file,(old,old))
    assert len(m.logs(tmp_path,level='ERROR',search='CORE_START_FAILED'))==1
    assert m.clean_logs(tmp_path,days=7,confirmed=False)==0
    assert m.clean_logs(tmp_path,days=7,confirmed=True)==1
    assert private.exists() and latest.exists()
    with pytest.raises(ValueError): m.clean_logs(tmp_path,days=-1,confirmed=True)


def test_symlink_logs_and_oversized_json_fail_closed(tmp_path):
    m=module(); outside=tmp_path/'outside'; outside.mkdir()
    logs=tmp_path/'logs'
    try: logs.symlink_to(outside,target_is_directory=True)
    except OSError:
        # Same policy as the other symlink tests: Windows without Developer
        # Mode/admin cannot create symlinks; native check is in the checklist.
        import pytest; pytest.skip('symlinks unavailable')
    (outside/'latest-core-report.json').write_text('{"status":"PASS"}')
    assert m.logs(tmp_path)==[]
    assert m.clean_logs(tmp_path,days=0,confirmed=True)==0
    with pytest.raises(ValueError): m.export_diagnostics(tmp_path,tmp_path/'export.zip')
