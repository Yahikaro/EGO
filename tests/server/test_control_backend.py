"""Qt-independent control behavior; external Core calls are boundary doubles."""
import importlib
import json
from pathlib import Path
import sys
import threading
import time


def backend(root):
    path = Path(__file__).parents[2]/'windows_ui/backend.py'
    assert path.exists(), 'Qt-independent control backend is missing'
    return importlib.import_module('windows_ui.backend').ControlBackend(root)


def test_status_distinguishes_uninitialized_without_creating_private_data(tmp_path):
    result = backend(tmp_path).execute('status')
    assert result['status'] == 'BLOCKED'
    assert result['code'] == 'CORE_UNINITIALIZED'
    assert not (tmp_path/'data').exists()


def test_safe_errors_and_cancel_before_dispatch(tmp_path, monkeypatch):
    b = backend(tmp_path)
    import server.ego_core.control as core
    def fail(root):
        raise RuntimeError('Bearer private-token conversation')
    monkeypatch.setattr(core, 'initialize', fail)
    result = b.execute('initialize')
    assert result['status'] == 'FAIL'
    assert 'private-token' not in json.dumps(result)
    event = threading.Event(); event.set()
    assert b.execute('initialize', cancel=event)['status'] == 'CANCELLED'
    assert b.execute('untrusted')['code'] == 'ACTION_UNSUPPORTED'


def test_history_contains_metadata_only_and_is_bounded(tmp_path):
    b = backend(tmp_path)
    b.execute('gpu_verify', payload={'content': 'private conversation'})
    rows = b.history()
    assert rows[-1]['action'] == 'gpu_verify'
    assert rows[-1]['status'] == 'BLOCKED'
    assert 'private conversation' not in (tmp_path/'settings/test-history.json').read_text()
    assert set(rows[-1]) <= {'action','status','code','started_at','duration_seconds','log'}


def test_test_runner_uses_saved_root_argument_list_and_retains_exit(tmp_path, monkeypatch):
    b = backend(tmp_path)
    seen = []
    def run(command, *, progress, cancel, timeout):
        seen.extend(command)
        return {'status': 'FAIL', 'code': 'TASK_FAILED', 'exit_code': 17}
    monkeypatch.setattr(b, '_run_process', run)
    result = b.execute('runtime_tests')
    assert result['status'] == 'FAIL' and result['exit_code'] == 17
    assert seen[1:] == [str(tmp_path/'tools/windows/runner.py'), 'test', '--suite', 'runtime']


def test_owned_process_cancellation_ends_spawned_fixture_worker(tmp_path):
    b = backend(tmp_path)
    marker = tmp_path/'worker.pid'
    script = tmp_path/'fixture.py'
    script.write_text("import subprocess,sys,time\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\nopen(sys.argv[1],'w').write(str(p.pid))\ntime.sleep(60)\n")
    event = threading.Event(); result = {}
    thread = threading.Thread(target=lambda: result.update(b._run_process(
        [sys.executable,str(script),str(marker)],progress=None,cancel=event,timeout=10)))
    thread.start()
    deadline=time.monotonic()+5
    while not marker.exists() and time.monotonic()<deadline: time.sleep(.02)
    assert marker.exists()
    pid=int(marker.read_text()); event.set(); thread.join(6)
    assert not thread.is_alive() and result['status']=='CANCELLED'
    import os
    if os.name=='nt':
        import psutil
        assert not psutil.pid_exists(pid)
    else:
        # The worker cannot run after its owned process group is killed. A
        # namespace init may retain its zombie until asynchronously reaped.
        with __import__('pytest').raises(ProcessLookupError): os.kill(pid,0)


def test_gpu_generation_is_never_claimed_as_offload(tmp_path):
    result=backend(tmp_path).execute('gpu_verify')
    assert result['status']=='BLOCKED'
    assert result['code']=='GPU_OFFLOAD_NOT_VERIFIED'


def _configured(tmp_path):
    from server.ego_core.config import CoreConfig,save_config
    data=tmp_path/'data'; data.mkdir(exist_ok=True)
    model=tmp_path/'local модель.gguf'; model.write_bytes(b'GGUFtest')
    config=CoreConfig(binary=sys.executable,model=str(model),startup_timeout=1.)
    save_config(data/'config.json',config)
    return config


def test_smoke_reuses_config_is_cancellable_and_never_returns_generated_text(tmp_path,monkeypatch):
    b=backend(tmp_path); _configured(tmp_path)
    import server.ego_core.control as core
    event=threading.Event(); calls=[]
    def request(root,method,path,**kwargs):
        calls.append((method,path,kwargs.get('payload')))
        if path=='/v1/system/status': return {'runtime':'idle'}
        if path=='/v1/chats': return {'id':'chat','version':1}
        if path=='/v1/generations': event.set(); return {'generation':{'id':'generation'}}
        if path.endswith('/stop'): return {'state':'stopped'}
        return {'state':'completed','text':'private generated text'}
    monkeypatch.setattr(core,'request',request)
    result=b.execute('smoke',cancel=event)
    assert result['status']=='CANCELLED'
    assert any(path=='/v1/generations/generation/stop' for _,path,_ in calls)
    assert 'private generated text' not in json.dumps(result)


def test_smoke_marks_working_only_after_actual_pass(tmp_path,monkeypatch):
    b=backend(tmp_path); _configured(tmp_path)
    from windows_ui.configuration import ConfigurationManager
    import server.ego_core.control as core
    manager=ConfigurationManager(tmp_path)
    def request(root,method,path,**kwargs):
        if path=='/v1/system/status': return {'runtime':'idle'}
        if path=='/v1/chats': return {'id':'chat','version':1}
        if path=='/v1/generations': return {'generation':{'id':'generation'}}
        return {'state':'completed','text':'private generated text'}
    monkeypatch.setattr(core,'request',request)
    result=b.execute('smoke')
    assert result['status']=='PASS' and result['output_characters']==22
    assert result['gpu_execution']=='NOT_VERIFIED'
    assert 'private generated text' not in json.dumps(result)
    assert manager.snapshot()['last_working'] is True


def test_benchmark_uses_saved_paths_and_missing_files_are_blocked(tmp_path,monkeypatch):
    b=backend(tmp_path); config=_configured(tmp_path); commands=[]
    def run(command,**kwargs): commands.append(command); return {'status':'PASS','code':'TASK_COMPLETED'}
    monkeypatch.setattr(b,'_run_process',run)
    assert b.execute('benchmark')['status']=='PASS'
    assert commands[0][-4:]==['--binary',config.binary,'--model',config.model]
    Path(config.model).unlink()
    result=b.execute('benchmark')
    assert result['status']=='BLOCKED' and result['code']=='MODEL_NOT_PROVIDED'
    assert len(commands)==1


def test_apply_requires_explicit_running_restart_and_rolls_back_failed_new_start(tmp_path,monkeypatch):
    b=backend(tmp_path); original=_configured(tmp_path)
    from server.ego_core.config import load_config
    from windows_ui.configuration import ConfigurationManager
    import server.ego_core.control as core
    manager=ConfigurationManager(tmp_path)
    candidate=manager.save_profile('Candidate',{**original.model_dump(),'context':4096,'api_port':18444})
    monkeypatch.setattr(b,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':{'runtime':'idle'}})
    calls=[]
    monkeypatch.setattr(core,'stop',lambda root:calls.append('stop') or {'status':'PASS','code':'CORE_STOPPED'})
    def start(root):
        calls.append(load_config(root/'data/config.json').context)
        if calls[-1]==4096: raise core.ControlFailure('CORE_START_FAILED')
        return {'status':'PASS','code':'CORE_STARTED'}
    monkeypatch.setattr(core,'start',start)
    result=b.execute('apply_profile',payload={'id':candidate['id']})
    assert result['status']=='BLOCKED' and result['code']=='RESTART_CONFIRMATION_REQUIRED'
    assert calls==[]
    result=b.execute('apply_profile',payload={'id':candidate['id'],'restart':True})
    assert result['status']=='FAIL' and result['code']=='PROFILE_APPLY_FAILED'
    assert load_config(tmp_path/'data/config.json')==original
    assert calls==['stop',4096,2048]
    assert result['details']['rollback_status']=='PASS'


def test_backend_exposes_sanitized_log_operations(tmp_path):
    b=backend(tmp_path)
    b.execute('gpu_verify')
    assert b.logs(level='WARNING',search='GPU_OFFLOAD_NOT_VERIFIED')
    assert b.export_diagnostics(tmp_path/'report.zip').is_file()
    assert b.clean_logs(days=30,confirmed=False)==0


def test_real_process_exit_is_retained_without_false_pass(tmp_path):
    b=backend(tmp_path)
    result=b._run_process([sys.executable,'-c','raise SystemExit(17)'],progress=None,cancel=None,timeout=3)
    assert result['status']=='FAIL' and result['exit_code']==17
    result=b._run_process([sys.executable,'-c','pass'],progress=None,cancel=None,timeout=3)
    assert result['status']=='PASS' and result['exit_code']==0


def test_report_parse_cannot_override_actual_nonzero_exit_or_accept_stale_report(tmp_path):
    b=backend(tmp_path); logs=tmp_path/'logs'; logs.mkdir()
    report=logs/'latest-benchmark-report.json'
    report.write_text(json.dumps({'status':'PASS','measurements':[{'context':2048,'first':{'tokens_predicted':10,'elapsed_seconds':1.25}}],'prompt':'private'}))
    assert hasattr(b,'_task_report'), 'Fresh safe task report parsing is missing'
    result=b._task_report('benchmark',{'status':'FAIL','code':'TASK_FAILED','exit_code':7},0)
    assert result['status']=='FAIL' and result['exit_code']==7
    assert result['details']['measurements'][0]['first']['tokens_predicted']==10
    assert 'private' not in json.dumps(result)
    assert 'details' not in b._task_report('benchmark',{'status':'PASS','code':'TASK_COMPLETED','exit_code':0},time.time()+5)


def test_failed_smoke_does_not_mark_working(tmp_path,monkeypatch):
    b=backend(tmp_path); _configured(tmp_path)
    from windows_ui.configuration import ConfigurationManager
    import server.ego_core.control as core
    manager=ConfigurationManager(tmp_path)
    def request(root,method,path,**kwargs):
        if path=='/v1/system/status': return {'runtime':'idle'}
        if path=='/v1/chats': return {'id':'chat','version':1}
        if path=='/v1/generations': return {'generation':{'id':'generation'}}
        return {'state':'failed','text':'private','error_code':'RUNTIME_START_FAILED'}
    monkeypatch.setattr(core,'request',request)
    assert b.execute('smoke')['status']=='FAIL'
    assert manager.snapshot()['last_working'] is False


def test_validation_failure_never_stops_running_core(tmp_path,monkeypatch):
    b=backend(tmp_path); original=_configured(tmp_path)
    from windows_ui.configuration import ConfigurationManager
    import server.ego_core.control as core
    manager=ConfigurationManager(tmp_path)
    candidate=manager.save_profile('Candidate',original.model_dump())
    Path(original.model).unlink()
    stopped=[]
    monkeypatch.setattr(b,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':{'runtime':'idle'}})
    monkeypatch.setattr(core,'stop',lambda root:stopped.append(True))
    result=b.execute('apply_profile',payload={'id':candidate['id'],'restart':True})
    assert result['status']=='BLOCKED' and result['code']=='MODEL_NOT_PROVIDED'
    assert not stopped


def test_core_start_cancel_terminates_only_its_new_child(tmp_path,monkeypatch):
    import io
    import server.ego_core.control as core
    event=threading.Event(); calls=[]
    monkeypatch.setattr(core.KeyVault,'unlock',lambda self:None)
    monkeypatch.setattr(core,'load_config',lambda path:None)
    # Startup check (stage 4d) is not under test here; it must not depend on a real EGO using port 18443.
    monkeypatch.setattr('server.ego_core.preflight.run',lambda root,**k:{'ok':True,'errors':[],'warnings':[]})
    def offline(*args,**kwargs): raise core.ControlFailure('CORE_OFFLINE')
    monkeypatch.setattr(core,'request',offline)
    class Input(io.BytesIO):
        def close(self): calls.append(self.getvalue()); super().close()
    class Child:
        stdin=Input()
        def poll(self): event.set(); return None
        def terminate(self): raise AssertionError('Graceful startup control must reap the owned Runtime first')
        def wait(self,timeout): calls.append('wait'); return -15
    monkeypatch.setattr(core.subprocess,'Popen',lambda *args,**kwargs:Child())
    import inspect
    assert 'cancel' in inspect.signature(core.start).parameters, 'Core startup cannot yet cancel its owned child'
    result=core.start(tmp_path,cancel=event)
    assert result['status']=='CANCELLED' and result['code']=='CORE_START_CANCELLED'
    assert calls==[b'STOP\n','wait']


def test_core_start_precancel_does_not_touch_existing_server(tmp_path,monkeypatch):
    import server.ego_core.control as core
    event=threading.Event(); event.set()
    touched=[]
    monkeypatch.setattr(core,'request',lambda *args,**kwargs:touched.append('request'))
    import inspect
    assert 'cancel' in inspect.signature(core.start).parameters
    assert core.start(tmp_path,cancel=event)['status']=='CANCELLED'
    assert touched==[]


def test_profile_selection_failure_stops_new_server_before_restoring_old(tmp_path,monkeypatch):
    b=backend(tmp_path); original=_configured(tmp_path)
    from server.ego_core.config import load_config
    from windows_ui.configuration import ConfigurationManager,ConfigurationFailure
    import server.ego_core.control as core
    manager=ConfigurationManager(tmp_path); profile=manager.save_profile('new',{**original.model_dump(),'context':4096,'api_port':18444})
    monkeypatch.setattr(b,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':{'runtime':'idle'}})
    calls=[]
    monkeypatch.setattr(core,'stop',lambda root:calls.append(('stop',load_config(root/'data/config.json').context)) or {'status':'PASS','code':'CORE_STOPPED'})
    monkeypatch.setattr(core,'start',lambda root:calls.append(('start',load_config(root/'data/config.json').context)) or {'status':'PASS','code':'CORE_STARTED'})
    def broken(self,profile_id): raise ConfigurationFailure('CONFIG_WRITE_FAILED')
    monkeypatch.setattr(ConfigurationManager,'select_profile',broken)
    result=b.execute('apply_profile',payload={'id':profile['id'],'restart':True})
    assert result['status']=='FAIL'
    assert calls==[('stop',2048),('start',4096),('stop',4096),('start',2048)]
    assert load_config(tmp_path/'data/config.json')==original


def test_failed_poll_stops_only_smoke_generation_before_returning_error(tmp_path,monkeypatch):
    b=backend(tmp_path); _configured(tmp_path)
    import server.ego_core.control as core
    stopped=[]
    def request(root,method,path,**kwargs):
        if path=='/v1/system/status': return {'runtime':'idle'}
        if path=='/v1/chats': return {'id':'chat','version':1}
        if path=='/v1/generations': return {'generation':{'id':'owned'}}
        if path=='/v1/generations/owned/stop': stopped.append(True); return {'state':'stopped'}
        raise core.ControlFailure('CORE_NETWORK_ERROR')
    monkeypatch.setattr(core,'request',request)
    assert b.execute('smoke')['code']=='CORE_NETWORK_ERROR'
    assert stopped==[True]


def test_start_keeps_core_available_when_saved_model_is_missing(tmp_path,monkeypatch):
    b=backend(tmp_path); config=_configured(tmp_path); Path(config.model).unlink()
    import server.ego_core.control as core
    touched=[]
    monkeypatch.setattr(core,'start',lambda *args,**kwargs:touched.append(True) or {'status':'PASS','code':'CORE_STARTED'})
    result=b.execute('start')
    assert result['status']=='PASS' and result['code']=='CORE_STARTED'
    assert touched==[True]


def test_history_rejects_untrusted_free_text_and_foreign_log_references(tmp_path):
    b=backend(tmp_path); settings=tmp_path/'settings'; settings.mkdir()
    (settings/'test-history.json').write_text(json.dumps([{'action':'private conversation','status':'PASS','code':'secret-token','started_at':'private','duration_seconds':-1,'log':'/private/user.txt'}]))
    assert b.history()==[]

def test_uninitialized_stop_is_success_without_unlocking_or_spawning(tmp_path,monkeypatch):
    b=backend(tmp_path)
    import server.ego_core.control as core
    def unexpected(root): raise AssertionError('An uninitialized Core has no vault to unlock')
    monkeypatch.setattr(core,'stop',unexpected)
    result=b.execute('stop')
    assert result['status']=='PASS' and result['code']=='CORE_ALREADY_STOPPED'
    assert not (tmp_path/'data').exists()

def test_apply_commits_exact_preflight_candidate_when_saved_profile_changes(tmp_path,monkeypatch):
    b=backend(tmp_path); original=_configured(tmp_path)
    from server.ego_core.config import load_config
    from windows_ui.configuration import ConfigurationManager
    import server.ego_core.control as core
    manager=ConfigurationManager(tmp_path)
    profile=manager.save_profile('Candidate',{**original.model_dump(),'gpu_layers':0,'api_port':18444})
    monkeypatch.setattr(b,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':{'runtime':'idle'}})
    def stop(root):
        manager.save_profile('Changed during apply',{**original.model_dump(),'gpu_layers':999,'threads':7},profile['id'])
        return {'status':'PASS','code':'CORE_STOPPED'}
    monkeypatch.setattr(core,'stop',stop)
    monkeypatch.setattr(core,'start',lambda root:{'status':'PASS','code':'CORE_STARTED'})
    result=b.execute('apply_profile',payload={'id':profile['id'],'restart':True})
    assert result['status']=='PASS'
    applied=load_config(tmp_path/'data/config.json')
    assert applied.gpu_layers==0 and applied.threads is None

def test_stop_never_treats_existing_inaccessible_storage_as_uninitialized(tmp_path,monkeypatch):
    b=backend(tmp_path)
    import server.ego_core.control as core
    from server.ego_core.keys import KeyFailure
    data=tmp_path/'data'; data.mkdir(); (data/'main.db').write_bytes(b'encrypted retained')
    def locked(root): raise KeyFailure('VAULT_MISSING')
    monkeypatch.setattr(core,'stop',locked)
    result=b.execute('stop')
    assert result['status']=='FAIL' and result['code']=='VAULT_MISSING'
    assert (data/'main.db').read_bytes()==b'encrypted retained'

def test_uninitialized_stop_does_not_ignore_an_active_data_lease(tmp_path):
    b=backend(tmp_path)
    import server.ego_core.control as core
    data=tmp_path/'data'; data.mkdir()
    with core.CoreLease(data):
        result=b.execute('stop')
    assert result['status']=='BLOCKED' and result['code']=='CORE_LOCKED'
