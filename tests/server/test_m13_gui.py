"""M1.3 backend contracts and real offscreen Qt lifecycle/status checks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import threading
import time
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication, QPushButton
from server.ego_core import control
from server.ego_core.config import CoreConfig, load_config, save_config
from windows_ui.backend import ControlBackend
from windows_ui.configuration import ConfigurationManager, ConfigurationFailure
from windows_ui.window import ControlCenterWindow


def configured(root):
    (root/'data').mkdir()
    binary=root/'llama-server'; binary.write_text('fixture'); binary.chmod(0o700)
    model=root/'model.gguf'; model.write_bytes(b'GGUFfixture')
    config=CoreConfig(binary=str(binary),model=str(model),gpu_layers=0)
    save_config(root/'data/config.json',config)
    return config


def test_auto_start_initializes_only_genuinely_fresh_data(tmp_path, monkeypatch):
    calls=[]
    def initialize(root):
        calls.append('initialize'); (root/'data').mkdir(exist_ok=True)
        return {'status':'PASS','code':'CORE_INITIALIZED'}
    monkeypatch.setattr(control,'initialize',initialize)
    monkeypatch.setattr(control,'start',lambda root,**kw:calls.append('start') or {'status':'PASS','code':'CORE_STARTED'})
    assert ControlBackend(tmp_path).execute('auto_start')['status']=='PASS'
    assert calls==['initialize','start']
    calls.clear(); (tmp_path/'data/main.db').write_bytes(b'existing encrypted data')
    result=ControlBackend(tmp_path).execute('auto_start')
    assert result['code']=='CORE_LOCKED' and calls==[]


def test_auto_start_preserves_existing_vault_and_saved_active_config(tmp_path, monkeypatch):
    original=configured(tmp_path); (tmp_path/'data'/control.KeyVault.VAULT).write_bytes(b'locked identity')
    manager=ConfigurationManager(tmp_path)
    saved=manager.save_profile('Other',{**original.model_dump(),'temperature':.9}); manager.select_profile(saved['id'])
    monkeypatch.setattr(control,'initialize',lambda root:pytest.fail('must not reset vault'))
    def start(root,**kw):
        assert load_config(root/'data/config.json')==original
        raise control.ControlFailure('CORE_LOCKED')
    monkeypatch.setattr(control,'start',start)
    assert ControlBackend(tmp_path).execute('auto_start')['code']=='CORE_LOCKED'


def test_auto_start_uses_valid_selected_profile_when_active_model_absent(tmp_path,monkeypatch):
    original=configured(tmp_path); manager=ConfigurationManager(tmp_path)
    profile=manager.save_profile('Saved',original.model_dump()); manager.select_profile(profile['id'])
    save_config(tmp_path/'data/config.json',CoreConfig())
    (tmp_path/'data'/control.KeyVault.VAULT).write_bytes(b'identity')
    monkeypatch.setattr(control.KeyVault,'unlock',lambda self:None)  # Windows DPAPI boundary only.
    seen=[]
    monkeypatch.setattr(control,'start',lambda root,**kw:seen.append(load_config(root/'data/config.json')) or {'status':'PASS','code':'CORE_STARTED'})
    assert ControlBackend(tmp_path).execute('auto_start')['status']=='PASS'
    assert seen==[original]


def test_bad_model_does_not_prevent_core_start(tmp_path,monkeypatch):
    original=configured(tmp_path); Path(original.model).unlink(); called=[]
    monkeypatch.setattr(control,'start',lambda root,**kw:called.append(True) or {'status':'PASS','code':'CORE_STARTED'})
    assert ControlBackend(tmp_path).execute('start')['status']=='PASS' and called==[True]


def test_unsupported_selected_profile_keeps_editable_core_available(tmp_path,monkeypatch):
    from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeFailure
    original=configured(tmp_path)
    manager=ConfigurationManager(tmp_path)
    profile=manager.save_profile('Unsupported',original.model_dump())
    manager.select_profile(profile['id'])
    save_config(tmp_path/'data/config.json',CoreConfig())
    (tmp_path/'data'/control.KeyVault.VAULT).write_bytes(b'existing identity')
    def unsupported(self): raise RuntimeFailure('UNSUPPORTED_RUNTIME_OPTION')
    monkeypatch.setattr(LlamaRuntime,'command',unsupported)
    seen=[]
    monkeypatch.setattr(control,'start',lambda root,**kw:seen.append(load_config(root/'data/config.json')) or {'status':'PASS','code':'CORE_STARTED'})
    assert ControlBackend(tmp_path).execute('auto_start')['status']=='PASS'
    assert seen==[CoreConfig()]


def test_runtime_restart_uses_authenticated_control_boundary(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(control,'request',lambda root,method,path,**kw:calls.append((method,path,kw['payload'])) or {'runtime_state':'LOADING','runtime_pid':42,'model_ready':False})
    result=ControlBackend(tmp_path).execute('runtime_restart')
    assert result['status']=='PASS' and calls==[('POST','/v1/runtime/restart',{})]
    assert result['core']['runtime_state']=='LOADING'


@pytest.mark.parametrize('changes',[{'temperature':.7,'top_p':.8,'top_k':22,'max_tokens':220,'repeat_penalty':1.1},{'context':4096}])
def test_profile_apply_uses_runtime_configure_for_same_core(tmp_path,monkeypatch,changes):
    original=configured(tmp_path); manager=ConfigurationManager(tmp_path)
    profile=manager.save_profile('Candidate',{**original.model_dump(),**changes})
    backend=ControlBackend(tmp_path); monkeypatch.setattr(backend,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':{'runtime_state':'READY'}})
    monkeypatch.setattr(control,'stop',lambda root:pytest.fail('Core must stay running'))
    monkeypatch.setattr(control,'start',lambda root,**kw:pytest.fail('Core must stay running'))
    calls=[]
    monkeypatch.setattr(control,'request',lambda root,method,path,**kw:calls.append((path,kw['payload'])) or {'runtime_pid':42,'runtime_state':'READY'})
    result=backend.execute('apply_profile',payload={'id':profile['id']})
    assert result['status']=='PASS'
    assert calls==[('/v1/runtime/configure',profile['values'])]
    assert load_config(tmp_path/'data/config.json').model_dump()==profile['values']


def test_busy_runtime_rejection_restores_saved_configuration(tmp_path,monkeypatch):
    original=configured(tmp_path); manager=ConfigurationManager(tmp_path)
    profile=manager.save_profile('Candidate',{**original.model_dump(),'context':4096})
    backend=ControlBackend(tmp_path); monkeypatch.setattr(backend,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':{'runtime_state':'READY'}})
    def request(*args,**kwargs): raise control.ControlFailure('CORE_BUSY')
    monkeypatch.setattr(control,'request',request)
    result=backend.execute('apply_profile',payload={'id':profile['id']})
    assert result['code']=='CORE_BUSY' and load_config(tmp_path/'data/config.json')==original
    assert manager.snapshot()['selected_id']!=profile['id']


@pytest.fixture
def widget(tmp_path):
    app=QApplication.instance() or QApplication([])
    from tests.server.test_control_window import FakeBackend
    backend=FakeBackend(); value=ControlCenterWindow(tmp_path,backend=backend); value.show(); app.processEvents()
    yield value,backend,app
    value.poll_timer.stop(); value._allow_close=True; value.close(); value.pool.waitForDone(3000); value.deleteLater(); app.processEvents()


def test_real_widget_runtime_state_pid_readiness_and_unknown(widget):
    value,backend,app=widget
    value._polled({'status_result':{'status':'PASS','code':'CORE_STATUS','core':{'runtime':'idle','runtime_state':'READY','runtime_pid':456,'model_ready':True}}})
    assert 'READY' in value.dashboard_values['runtime'].text()
    assert '456' in value.dashboard_values['runtime'].text()
    assert '456' in value.monitor_values['runtime_listener'].text()
    value._polled({'status_result':{'status':'PASS','code':'CORE_STATUS','core':{'runtime_state':'STOPPED','runtime_pid':None,'model_ready':False}}})
    assert 'STOPPED' in value.dashboard_values['runtime'].text()
    value._polled({'status_result':{'status':'BLOCKED','code':'CORE_LOCKED','core':{'state':'LOCKED'}}})
    assert '456' not in value.dashboard_values['runtime'].text()
    assert 'STOPPED' not in value.dashboard_values['runtime'].text()


def test_real_widget_runtime_restart_button_dispatches(widget):
    value,backend,app=widget
    button=next((b for b in value.findChildren(QPushButton) if b.property('runtime_restart')),None)
    assert button is not None
    button.click()
    until=time.monotonic()+2
    while value.busy and time.monotonic()<until: app.processEvents(); time.sleep(.005)
    assert any(call[0]=='runtime_restart' for call in backend.calls)


def test_production_widget_schedules_autostart_once(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    calls=[]; threads=[]
    original=ControlBackend.execute
    def execute(self,action,**kwargs):
        if action=='auto_start': calls.append(action); threads.append(threading.current_thread().name); return {'status':'PASS','code':'CORE_STARTED'}
        if action=='status': return {'status':'PASS','code':'CORE_STATUS','core':{'state':'OFFLINE'}}
        if action=='monitor': return {'status':'PASS','code':'MONITOR_SNAPSHOT','details':{}}
        return original(self,action,**kwargs)
    monkeypatch.setattr(ControlBackend,'execute',execute)
    monkeypatch.setattr(ControlCenterWindow,'show_setup',lambda self:None)
    value=ControlCenterWindow(tmp_path); value.show()
    until=time.monotonic()+2
    while (not calls or value.busy) and time.monotonic()<until: app.processEvents(); time.sleep(.005)
    value.poll(); value.hide(); value.show(); app.processEvents()
    assert calls==['auto_start'] and threads[0]!=threading.current_thread().name
    value.poll_timer.stop(); value._allow_close=True; value.close(); value.pool.waitForDone(3000); value.deleteLater(); app.processEvents()


def test_selection_write_failure_does_not_reload_live_runtime(tmp_path,monkeypatch):
    original=configured(tmp_path); manager=ConfigurationManager(tmp_path)
    profile=manager.save_profile('Candidate',{**original.model_dump(),'context':4096})
    backend=ControlBackend(tmp_path); monkeypatch.setattr(backend,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':{'runtime_state':'READY'}})
    calls=[]
    monkeypatch.setattr(control,'request',lambda *args,**kw:calls.append(True) or {})
    def broken(self,profile_id): raise ConfigurationFailure('CONFIG_WRITE_FAILED')
    monkeypatch.setattr(ConfigurationManager,'select_profile',broken)
    result=backend.execute('apply_profile',payload={'id':profile['id']})
    assert result['status']=='FAIL' and result['details']['rollback_status']=='PASS'
    assert load_config(tmp_path/'data/config.json')==original and calls==[]


def test_runtime_apply_keeps_preflight_candidate_when_registry_changes(tmp_path,monkeypatch):
    original=configured(tmp_path); manager=ConfigurationManager(tmp_path)
    profile=manager.save_profile('Candidate',{**original.model_dump(),'temperature':.7})
    backend=ControlBackend(tmp_path); monkeypatch.setattr(backend,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':{'runtime_state':'READY'}})
    payloads=[]
    def request(root,method,path,**kw):
        manager.save_profile('Concurrent edit',{**original.model_dump(),'temperature':.9},profile['id'])
        payloads.append(kw['payload'])
        return {'runtime_state':'READY','runtime_pid':42}
    monkeypatch.setattr(control,'request',request)
    assert backend.execute('apply_profile',payload={'id':profile['id']})['status']=='PASS'
    assert payloads==[profile['values']]
    assert load_config(tmp_path/'data/config.json').temperature==.7


def test_autoload_never_mutates_config_before_existing_identity_unlock(tmp_path,monkeypatch):
    original=configured(tmp_path); manager=ConfigurationManager(tmp_path)
    profile=manager.save_profile('Saved',original.model_dump()); manager.select_profile(profile['id'])
    empty=CoreConfig(); save_config(tmp_path/'data/config.json',empty)
    (tmp_path/'data'/control.KeyVault.VAULT).write_bytes(b'inaccessible identity')
    def locked(self): raise control.ControlFailure('CORE_LOCKED')
    monkeypatch.setattr(control.KeyVault,'unlock',locked)
    monkeypatch.setattr(control,'start',lambda *args,**kw:pytest.fail('unlock must precede config mutation'))
    assert ControlBackend(tmp_path).execute('auto_start')['code']=='CORE_LOCKED'
    assert load_config(tmp_path/'data/config.json')==empty


def test_real_widget_generation_options_apply_without_core_restart_prompt(widget,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    value,backend,app=widget; original=configured(value.root)
    profile=value.configuration.save_profile('Sampling',{**original.model_dump(),'temperature':.7})
    value.configuration.select_profile(profile['id']); value.reload_profiles(); value._online=True
    monkeypatch.setattr(QMessageBox,'question',lambda *args,**kw:pytest.fail('Same-Core changes must not prompt to stop generation'))
    value.apply_profile()
    until=time.monotonic()+2
    while value.busy and time.monotonic()<until: app.processEvents(); time.sleep(.005)
    assert ('apply_profile',{'id':profile['id'],'restart':False}) in [(call[0],call[2]) for call in backend.calls]


def test_failed_stop_notification_is_nonblocking_and_keeps_window_open(widget):
    value,backend,app=widget
    value._stop_close=True; value._action_name='stop'
    value._action_finished({'status':'FAIL','code':'CORE_STOP_TIMEOUT'})
    app.processEvents()
    assert value.isVisible() and not value._allow_close and value._shutdown_warning.isVisible()
    value._shutdown_warning.close()

def test_editing_model_parameters_preserves_existing_core_transport(tmp_path):
    from PySide6.QtWidgets import QApplication
    from windows_ui.window import ControlCenterWindow
    from server.ego_core.config import CoreConfig
    app=QApplication.instance() or QApplication([])
    # This test uses the same backend helper as the widget tests in this file.
    class Backend:
        def execute(self,*args,**kwargs):return {'status':'PASS','code':'CORE_STATUS','core':{'state':'OFFLINE'}}
        def history(self):return []
    window=ControlCenterWindow(tmp_path,backend=Backend())
    try:
        window._load_values(CoreConfig(local_http=False).model_dump(),'Existing TLS')
        window.model_fields['temperature'].setValue(.8)
        assert window._values().get('local_http',True) is False
        profile=window.save_profile()
        assert profile['values']['local_http'] is False
    finally:
        window.poll_timer.stop();window._allow_close=True;window.close();window.pool.waitForDone(3000);app.processEvents()

def test_stop_and_close_abandons_setup_queue_before_stopping_core(widget):
    value,backend,app=widget
    entered,release=threading.Event(),threading.Event()
    execute=backend.execute
    def delayed(action,**kwargs):
        if action=='apply_profile': entered.set();release.wait(2)
        return execute(action,**kwargs)
    backend.execute=delayed
    value.show_setup();value.wizard.binary.setText('/tmp/llama-server')
    value.wizard.model.setText('/tmp/fixture.gguf');value.wizard.trial()
    until=time.monotonic()+3
    while not entered.is_set() and time.monotonic()<until:app.processEvents();time.sleep(.005)
    assert entered.is_set()
    value.stop_and_close();release.set()
    until=time.monotonic()+4
    while not value._allow_close and time.monotonic()<until:app.processEvents();time.sleep(.005)
    actions=[call[0] for call in backend.calls if call[0] not in {'status','monitor'}]
    assert value._allow_close and not value.isVisible()
    assert actions==['apply_profile','stop']


def test_network_error_does_not_claim_encrypted_storage_is_locked(tmp_path, monkeypatch):
    from windows_ui.backend import ControlBackend
    from server.ego_core import control
    vault=tmp_path/'data'/control.KeyVault.VAULT
    vault.parent.mkdir(exist_ok=True)
    vault.write_bytes(b'existing-vault-marker')
    def fail(*args, **kwargs):
        raise control.ControlFailure('CORE_NETWORK_ERROR')
    monkeypatch.setattr(control, 'request', fail)
    result=ControlBackend(tmp_path)._status()
    assert result['code']=='CORE_NETWORK_ERROR'
    assert result['core']['state']=='OFFLINE'
