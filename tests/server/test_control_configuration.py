import importlib
import json
from pathlib import Path
import pytest


def api():
    return importlib.import_module('windows_ui.configuration')


def files(root):
    binary = root/'исполнитель space.exe'; binary.write_bytes(b'local')
    model = root/'модель space.gguf'; model.write_bytes(b'GGUFfixture')
    return {'binary': str(binary), 'model': str(model)}


def test_import_legacy_missing_files_remains_repairable(tmp_path):
    (tmp_path/'data').mkdir()
    legacy = {'binary': str(tmp_path/'missing.exe'), 'model': str(tmp_path/'missing.gguf')}
    (tmp_path/'data/config.json').write_text(json.dumps(legacy))
    manager = api().ConfigurationManager(tmp_path)
    state = manager.snapshot()
    assert len(state['profiles']) == 1
    assert state['core']['binary'] == legacy['binary']
    assert state['last_working'] is False
    assert state['error'] is None
    profile = manager.save_profile('Исправлено', files(tmp_path), state['selected_id'])
    assert manager.apply_profile(profile['id'])['model'].endswith('модель space.gguf')


def test_bad_config_never_silently_replaced(tmp_path):
    (tmp_path/'data').mkdir()
    path = tmp_path/'data/config.json'; path.write_text('{broken')
    state = api().ConfigurationManager(tmp_path).snapshot()
    assert state['error'] == 'CONFIG_INVALID'
    assert path.read_text() == '{broken'
    assert not state['last_working']


def test_apply_is_atomic_and_working_requires_explicit_smoke_mark(tmp_path):
    m = api().ConfigurationManager(tmp_path)
    first = m.save_profile('First', files(tmp_path))
    assert not m.snapshot()['last_working']
    m.apply_profile(first['id']); m.mark_working(first['id'])
    path = tmp_path/'data/config.json'; original = path.read_bytes()
    bad = m.save_profile('Candidate', {'binary': str(tmp_path/'missing.exe'), 'model': str(tmp_path/'missing.gguf')})
    with pytest.raises(api().ConfigurationFailure, match='RUNTIME_NOT_PROVIDED'):
        m.apply_profile(bad['id'])
    assert path.read_bytes() == original
    second = m.save_profile('Second', {**files(tmp_path), 'context': 4096})
    m.apply_profile(second['id'])
    assert m.restore_working()['context'] == 2048
    assert json.loads(path.read_text())['context'] == 2048
    assert api().ConfigurationManager(tmp_path).snapshot()['last_working']


def test_registry_removal_leaves_model_and_core_and_gui_persist(tmp_path):
    m = api().ConfigurationManager(tmp_path)
    p = m.save_profile('Local', files(tmp_path)); m.select_profile(p['id']); m.apply_profile(p['id'])
    m.save_gui({'language':'en','theme':'light','reduced_effects':True,'close_behavior':'hide'})
    m.remove_profile(p['id'])
    snapshot = api().ConfigurationManager(tmp_path).snapshot()
    assert snapshot['profiles'] == [] and snapshot['selected_id'] is None
    assert snapshot['gui']['language'] == 'en'
    assert Path(p['values']['model']).is_file()
    assert (tmp_path/'data/config.json').is_file()


def test_import_export_allowlisted_and_no_secrets(tmp_path):
    m = api().ConfigurationManager(tmp_path)
    p = m.save_profile('Local', files(tmp_path))
    dest = tmp_path/'profile.json'; m.export_profile(p['id'], dest)
    assert m.import_profile(dest)['values'] == p['values']
    obj = json.loads(dest.read_text()); obj['values']['api_token'] = 'secret'
    dest.write_text(json.dumps(obj))
    with pytest.raises(api().ConfigurationFailure, match='PROFILE_INVALID'):
        m.import_profile(dest)
    obj.pop('values'); obj['token'] = 'secret'; dest.write_text(json.dumps(obj))
    with pytest.raises(api().ConfigurationFailure, match='PROFILE_INVALID'):
        m.import_profile(dest)


def test_quoted_paths_and_unknown_gui_and_invalid_gguf(tmp_path):
    m = api().ConfigurationManager(tmp_path)
    values = files(tmp_path)
    profile = m.save_profile('Quoted', {k:'"'+v+'"' for k,v in values.items()})
    assert profile['values']['binary'] == values['binary']
    Path(values['model']).write_bytes(b'bad!')
    with pytest.raises(api().ConfigurationFailure, match='INVALID_GGUF'):
        m.apply_profile(profile['id'])
    with pytest.raises(api().ConfigurationFailure, match='GUI_INVALID'):
        m.save_gui({'api_key':'secret'})


def test_failed_atomic_apply_preserves_previous_config_and_working(tmp_path, monkeypatch):
    import server.ego_core.config as core
    m = api().ConfigurationManager(tmp_path)
    first = m.save_profile('Working',files(tmp_path)); m.apply_profile(first['id']); m.mark_working(first['id'])
    before = (tmp_path/'data/config.json').read_bytes()
    second = m.save_profile('Candidate',{**first['values'],'context':4096})
    def rejected(source,dest):
        raise OSError('synthetic atomic replace failure')
    monkeypatch.setattr(core.os,'replace',rejected)
    with pytest.raises(api().ConfigurationFailure,match='CONFIG_WRITE_FAILED'):
        m.apply_profile(second['id'])
    assert (tmp_path/'data/config.json').read_bytes() == before
    assert m.snapshot()['last_working']
    assert not list((tmp_path/'data').glob('.config-*.tmp'))


def test_working_captures_actual_config_and_missing_model_does_not_block_snapshot(tmp_path):
    m = api().ConfigurationManager(tmp_path)
    p = m.save_profile('Working',files(tmp_path)); m.apply_profile(p['id'])
    m.save_profile('Edited candidate',{**p['values'],'context':4096},p['id'])
    m.mark_working(p['id'])
    assert m.restore_working()['context'] == 2048
    Path(p['values']['model']).unlink()
    state = m.snapshot()
    assert state['error'] is None and state['core']['model'] == p['values']['model']


def test_corrupted_registry_is_reported_and_not_overwritten(tmp_path):
    m = api().ConfigurationManager(tmp_path)
    registry = tmp_path/'settings/profiles.json'; registry.write_text('{broken')
    assert m.snapshot()['error'] == 'PROFILE_REGISTRY_INVALID'
    with pytest.raises(api().ConfigurationFailure,match='PROFILE_REGISTRY_INVALID'):
        m.save_profile('Candidate',files(tmp_path))
    assert registry.read_text() == '{broken'


def test_symlink_settings_cannot_write_outside_root(tmp_path):
    outside = tmp_path/'outside'; outside.mkdir()
    root = tmp_path/'root'; root.mkdir()
    try:
        (root/'settings').symlink_to(outside,target_is_directory=True)
    except (OSError,NotImplementedError):
        pytest.skip('symlinks unavailable')
    m = api().ConfigurationManager(root)
    assert m.snapshot()['error'] == 'CONFIG_PATH_UNSAFE'
    with pytest.raises(api().ConfigurationFailure,match='CONFIG_PATH_UNSAFE'):
        m.save_profile('Candidate',files(root))
    assert list(outside.iterdir()) == []


def test_working_config_is_readonly_snapshot_of_actual_values(tmp_path):
    m = api().ConfigurationManager(tmp_path)
    assert m.working_config() is None
    p = m.save_profile('Working',files(tmp_path)); m.apply_profile(p['id']); m.mark_working()
    values = m.working_config(); values['context']=4096
    assert m.working_config()['context']==2048


def test_failed_settings_directory_creation_uses_structured_error(tmp_path, monkeypatch):
    m=api().ConfigurationManager(tmp_path)
    values=files(tmp_path)
    def reject(*args,**kwargs): raise OSError('synthetic permissions')
    monkeypatch.setattr(Path,'mkdir',reject)
    with pytest.raises(api().ConfigurationFailure,match='CONFIG_WRITE_FAILED'):
        m.save_profile('Candidate',values)

def test_profile_export_cannot_overwrite_encrypted_project_files(tmp_path):
    from windows_ui.configuration import ConfigurationManager, ConfigurationFailure
    manager=ConfigurationManager(tmp_path)
    profile=manager.save_profile('Test',{})
    data=tmp_path/'data'; data.mkdir()
    target=data/'config.json'; target.write_text('original')
    with pytest.raises(ConfigurationFailure) as error:
        manager.export_profile(profile['id'],target)
    assert error.value.code=='PROFILE_EXPORT_PATH_UNSAFE'
    assert target.read_text()=='original'

def test_profile_export_normalizes_parent_components_before_data_guard(tmp_path):
    from windows_ui.configuration import ConfigurationManager, ConfigurationFailure
    manager=ConfigurationManager(tmp_path); profile=manager.save_profile('Test',{})
    data=tmp_path/'data'; data.mkdir(); (tmp_path/'export').mkdir()
    target=data/'config.json'; target.write_text('original')
    with pytest.raises(ConfigurationFailure): manager.export_profile(profile['id'],tmp_path/'export/../data/config.json')
    assert target.read_text()=='original'
