"""GUI benchmark measures the owned warm runtime instead of spawning a rival."""
import secrets
import pytest
from server.ego_core.api import create_app
from server.ego_core.config import CoreConfig,save_config
from server.ego_core import control
from windows_ui.backend import ControlBackend
from test_core_api import LocalClient
from test_persistent_service import persistent,wait_for


def test_warm_gui_benchmark_keeps_core_child_and_uses_its_real_timings(persistent,tmp_path,monkeypatch):
    service,store,runtime=persistent
    service.start_runtime();wait_for(lambda:service.status()['runtime_state']=='READY')
    child=runtime.process
    data=tmp_path/'data';data.mkdir()
    save_config(data/'config.json',CoreConfig(binary=str(runtime.config.binary),model=str(runtime.config.model),runtime_port=runtime.config.port))
    token=secrets.token_urlsafe(32);client=LocalClient(create_app(service,token));client.headers['authorization']='Bearer '+token
    def request(root,method,path,*,payload=None,**kwargs):
        result=client.request(method,path,json=payload)
        if not result.is_success: raise control.ControlFailure(result.json()['code'])
        return result.json()
    monkeypatch.setattr(control,'request',request)
    backend=ControlBackend(tmp_path)
    monkeypatch.setattr(backend,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':service.status()})
    def forbidden(*args,**kwargs): pytest.fail('Benchmark must reuse loaded child, not start another llama')
    monkeypatch.setattr(backend,'_run_process',forbidden)
    result=backend.execute('benchmark')
    assert result['status']=='PASS' and result['code']=='WARM_RUNTIME_BENCHMARK'
    assert result['details']['tokens_per_second']==10
    assert result['details']['scope']=='warm_runtime_generation'
    assert result['gpu_execution']=='NOT_VERIFIED'
    assert runtime.process is child and child.poll() is None


def test_online_error_benchmark_never_launches_a_second_runtime(tmp_path,monkeypatch):
    backend=ControlBackend(tmp_path)
    monkeypatch.setattr(backend,'_status',lambda:{'status':'PASS','code':'CORE_STATUS','core':{'runtime_state':'ERROR'}})
    monkeypatch.setattr(backend,'_model_config',lambda:None)
    monkeypatch.setattr(backend,'_run_process',lambda *a,**k:pytest.fail('No cold benchmark beside persistent ERROR owner'))
    result=backend.execute('benchmark')
    assert result['status']=='BLOCKED' and result['code']=='RUNTIME_NOT_READY'
