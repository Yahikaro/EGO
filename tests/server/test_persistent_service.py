"""Persistent service behavior with real SQLCipher and disposable TCP children."""
from contextlib import contextmanager
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from uuid import uuid4
import pytest
from runtime.adapters.llama_cpp import LlamaRuntime,RuntimeConfig,RuntimeFailure,StreamEvent
from runtime.adapters.ports import choose_loopback_port
from server.ego_core.service import CoreService,CoreFailure
from server.ego_core.store import MainStore


def uid(): return str(uuid4())

def wait_for(predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>deadline: pytest.fail('Persistent service condition deadline')
        time.sleep(.01)

@pytest.fixture
def persistent(tmp_path,monkeypatch):
    model=tmp_path/'synthetic.gguf';model.write_bytes(b'GGUFfixture')
    runtime=LlamaRuntime(RuntimeConfig(Path(sys.executable),model,choose_loopback_port(),startup_timeout=3.))
    fixture=Path(__file__).parents[2]/'tests/fixtures/fake_llama.py'
    monkeypatch.setattr(runtime,'_binary_command',lambda:[getattr(sys,'_base_executable',sys.executable),str(fixture)])
    store=MainStore(tmp_path/'main.db',secrets.token_bytes(32))
    service=CoreService(store,runtime=runtime)
    yield service,store,runtime
    service.close();store.close()


def complete(service,store,chat,content='synthetic'):
    generation=service.generate(uid(),chat['id'],content,chat['version'])
    wait_for(lambda:store.generation(generation['id'])['state'] in {'completed','stopped','failed'})
    wait_for(lambda:service.status()['active_generation'] is None)
    return store.generation(generation['id'])


def test_preload_then_two_chats_reuse_exact_child_pid_and_keep_model(persistent):
    service,store,runtime=persistent
    service.start_runtime()
    wait_for(lambda:service.status().get('runtime_state')=='READY')
    first=runtime.process
    assert first is not None and service.status()['runtime_pid']==first.pid
    assert service.status()['model_ready'] is True
    for title in ['A','B']:
        chat=store.create_chat(uid(),title)
        result=complete(service,store,chat)
        assert result['state']=='completed' and result['text']=='xxx'
        assert runtime.process is first and first.poll() is None
        assert service.status()['runtime_state']=='READY'
    assert service.status()['generation_metrics']['tokens_per_second']==10
    assert service.status()['model_load_seconds']>=0


def test_soft_cancel_and_next_generation_keep_same_child(persistent):
    service,store,runtime=persistent;service.start_runtime()
    wait_for(lambda:service.status().get('runtime_state')=='READY')
    child=runtime.process
    chat=store.create_chat(uid(),'cancel')
    generation=service.generate(uid(),chat['id'],'slow',1)
    wait_for(lambda:len(store.generation(generation['id'])['text'])>0)
    service.stop(uid(),generation['id'])
    wait_for(lambda:service.status()['active_generation'] is None)
    assert store.generation(generation['id'])['state']=='stopped'
    assert runtime.process is child and child.poll() is None
    another=store.create_chat(uid(),'after cancel')
    assert complete(service,store,another)['state']=='completed'
    assert runtime.process is child


def test_concurrent_requests_are_queued_and_can_be_cancelled(persistent):
    service,store,runtime=persistent;service.start_runtime()
    wait_for(lambda:service.status().get('runtime_state')=='READY')
    first=store.create_chat(uid(),'first');second=store.create_chat(uid(),'second')
    generation=service.generate(uid(),first['id'],'slow',1)
    wait_for(lambda:service.status().get('runtime_state')=='BUSY')
    second_generation=service.generate(uid(),second['id'],'must stay queued',1)
    assert second_generation['state']=='queued'
    assert second_generation['id'] in service.status()['queued_generations']
    service.stop(uid(),second_generation['id'])
    assert store.generation(second_generation['id'])['state']=='stopped'
    assert len(store.messages(second['id']))==1  # accepted input stays in the chat
    service.stop(uid(),generation['id']);wait_for(lambda:service.status()['active_generation'] is None)


def test_explicit_restart_replaces_only_owned_child_and_close_reaps_it(persistent):
    service,store,runtime=persistent;service.start_runtime()
    wait_for(lambda:service.status().get('runtime_state')=='READY')
    old=runtime.process
    service.restart_runtime()
    wait_for(lambda:service.status().get('runtime_state')=='READY')
    new=runtime.process
    assert new is not old and old.poll() is not None and new.poll() is None
    service.close()
    assert new.poll() is not None and service.status()['runtime_state']=='STOPPED'
    service.close()


def test_missing_model_error_keeps_core_usable_and_never_retries(persistent):
    service,store,runtime=persistent
    runtime.config.model.unlink()
    service.start_runtime()
    wait_for(lambda:service.status().get('runtime_state')=='ERROR')
    assert service.status()['last_runtime_error']['code']=='MODEL_NOT_PROVIDED'
    chat=store.create_chat(uid(),'still editable')
    with pytest.raises(CoreFailure,match='RUNTIME_NOT_READY'):
        service.generate(uid(),chat['id'],'no repeated automatic load',1)
    time.sleep(.15)
    assert service.status()['runtime_state']=='ERROR' and runtime.process is None
    assert store.messages(chat['id'])==[]


def test_foreign_listener_error_keeps_original_os_diagnostics_and_owner_alive(persistent):
    service,store,runtime=persistent
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',runtime.config.port));listener.listen()
        service.start_runtime();wait_for(lambda:service.status().get('runtime_state')=='ERROR')
        error=service.status()['last_runtime_error']
        assert error['code']=='PORT_IN_USE' and error['errno'] is not None
        service.close()
        assert listener.fileno()>=0


def test_idle_child_crash_becomes_error_without_respawn(persistent):
    service,store,runtime=persistent;service.start_runtime()
    wait_for(lambda:service.status().get('runtime_state')=='READY')
    child=runtime.process;child.kill();child.wait(timeout=3)
    assert service.status()['runtime_state']=='ERROR'
    assert service.status()['model_ready'] is False
    time.sleep(.1);assert child.poll() is not None
    assert service.status()['runtime_pid'] is None

def test_failed_cuda_style_child_exit_is_reported_once_without_restart(persistent,monkeypatch):
    service,store,runtime=persistent
    monkeypatch.setattr(runtime,'_binary_command',lambda:[getattr(sys,'_base_executable',sys.executable),'-c','raise SystemExit(87)'])
    service.start_runtime();wait_for(lambda:service.status()['runtime_state']=='ERROR')
    assert service.status()['last_runtime_error']['code']=='RUNTIME_EXITED'
    assert service.status()['last_runtime_error']['exit_code']==87
    events=service.status()['runtime_events']
    service.start_runtime();time.sleep(.1)
    assert service.status()['runtime_events']==events
    assert runtime.process is None

def test_model_change_retains_owned_same_port_teardown_grace(persistent,monkeypatch):
    from dataclasses import replace
    service,store,runtime=persistent;service.start_runtime()
    wait_for(lambda:service.status()['runtime_state']=='READY')
    old=runtime.process;released=threading.Event();timers=[]
    fixture=Path(__file__).parents[2]/'tests/fixtures/fake_llama.py'
    def factory(config):
        # A real short-lived socket occupies the just-reaped endpoint. The
        # existing bounded own-teardown policy must survive adapter replacement.
        import os
        holder=socket.socket()
        if os.name=='nt':holder.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        else:holder.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        holder.bind(('127.0.0.1',config.port));holder.listen()
        def release():holder.close();released.set()
        timer=threading.Timer(.15,release);timer.start();timers.append(timer)
        replacement=LlamaRuntime(config)
        monkeypatch.setattr(replacement,'_binary_command',lambda:[getattr(sys,'_base_executable',sys.executable),str(fixture)])
        return replacement
    monkeypatch.setattr(service,'_runtime_factory',factory)
    service.reconfigure_runtime(replace(runtime.config,context=4096))
    wait_for(lambda:service.status()['runtime_state'] in {'READY','ERROR'})
    for timer in timers:timer.join(1)
    assert released.is_set() and old.poll() is not None
    assert service.status()['runtime_state']=='READY'
    assert service.runtime.process is not old
