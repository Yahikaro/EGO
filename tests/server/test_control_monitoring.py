import importlib
from pathlib import Path
from types import SimpleNamespace


def module():
    assert (Path(__file__).parents[2]/'windows_ui/monitoring.py').exists(), 'monitoring helper missing'
    return importlib.import_module('windows_ui.monitoring')


def test_nvidia_unavailable_leaves_nullable_measurements(tmp_path,monkeypatch):
    m=module()
    def missing(*args,**kwargs): raise FileNotFoundError('private path')
    monkeypatch.setattr(m.subprocess,'run',missing)
    result=m.snapshot(tmp_path)
    assert result['cpu_percent'] is not None
    assert result['ram_used_bytes']>0
    assert result['gpu_percent'] is None and result['vram_used_mib'] is None
    assert result['gpu_temperature_c'] is None
    assert result['errors']==['NVIDIA_SMI_UNAVAILABLE']
    assert 'private path' not in str(result)


def test_nvidia_numbers_and_listener_observation_never_imply_ownership(tmp_path,monkeypatch):
    m=module()
    monkeypatch.setattr(m.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout='0, 20, 1024, 8192, 50\n'))
    monkeypatch.setattr(m.psutil,'net_connections',lambda **kwargs:[SimpleNamespace(status='LISTEN',laddr=SimpleNamespace(ip='127.0.0.1',port=18081),pid=1234)])
    result=m.snapshot(tmp_path)
    assert result['gpu_percent']==20 and result['vram_used_mib']==1024
    assert result['gpu_temperature_c']==50
    assert result['runtime_listener']=={'pid':1234,'port':18081,'observed':True,'owned':False}
    assert result['tokens_per_second'] is None and result['model_load_seconds'] is None


def test_malformed_or_unavailable_gpu_values_are_unknown(tmp_path,monkeypatch):
    m=module()
    monkeypatch.setattr(m.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout='0, [N/A], wrong, 0, [Not Supported]\n'))
    result=m.snapshot(tmp_path)
    assert result['gpu_percent'] is None and result['vram_used_mib'] is None
    assert 'NVIDIA_SMI_INVALID_DATA' in result['errors']


def test_runtime_observation_is_read_only_and_never_claims_ownership(tmp_path,monkeypatch):
    m=module()
    monkeypatch.setattr(m.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout='0, 20, 1024, 8192, 50\n'))
    monkeypatch.setattr(m.psutil,'net_connections',lambda **kwargs:[SimpleNamespace(status='LISTEN',laddr=SimpleNamespace(ip='127.0.0.1',port=18081),pid=5678)])
    class Process:
        def __init__(self,pid): assert pid==5678
        def name(self): return 'llama-server.exe'
        def memory_info(self): return SimpleNamespace(rss=256*1048576)
        def create_time(self): return 0
    monkeypatch.setattr(m.psutil,'Process',Process)
    result=m.snapshot(tmp_path)
    assert result['runtime_listener']['owned'] is False
    assert result['runtime_process']=='LLAMA_SERVER_OBSERVED'
    assert result['runtime_memory_mib']==256
    assert result['runtime_uptime_seconds'] is not None
    assert 'commandline' not in str(result).lower()
