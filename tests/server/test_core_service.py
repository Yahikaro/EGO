"""Deterministic ownership/start handoffs around the real encrypted store."""
import secrets
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4
import pytest
from server.ego_core.store import MainStore
from server.ego_core.service import CoreService, CoreFailure
from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeConfig, RuntimeFailure, StreamEvent
from runtime.adapters.ports import choose_loopback_port


def uid(): return str(uuid4())

def until(predicate,seconds=3):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>=deadline: pytest.fail('condition deadline')
        time.sleep(.005)


def test_maximum_accepted_latest_input_is_never_omitted_by_history_budget(tmp_path):
    store=MainStore(tmp_path/'main.db',secrets.token_bytes(32)); service=CoreService(store)
    try:
        chat=store.create_chat(uid(),'history')
        first=store.begin_generation(uid(),chat['id'],'first',1)
        store.set_generation_state(first['id'],'started')
        store.set_generation_state(first['id'],'completed',text='answer')
        newest='Q'*32768
        store.begin_generation(uid(),chat['id'],newest,3)
        prompt=service._prompt(chat['id'])
        assert newest in prompt and len(prompt)<=32768
    finally: service.close(); store.close()


def test_delayed_stop_cannot_cancel_the_next_generation(tmp_path):
    first_ready,first_finish,cancel_enter,cancel_release,second_ready,second_finish=[threading.Event() for _ in range(6)]
    class Runtime:
        count=0; live=False; block_cancel=True
        def start(self): self.count+=1; self.live=True
        def shutdown(self): self.live=False
        def cancel(self):
            if self.block_cancel:
                self.block_cancel=False; cancel_enter.set(); assert cancel_release.wait(3)
            self.live=False
        def stream(self,prompt):
            if self.count==1:
                first_ready.set(); assert first_finish.wait(3)
            else:
                second_ready.set(); assert second_finish.wait(3)
            if not self.live: raise RuntimeFailure('STREAM_INTERRUPTED')
            yield StreamEvent('output',stop=True)
    runtime=Runtime()
    store=MainStore(tmp_path/'main.db',secrets.token_bytes(32)); service=CoreService(store,runtime=runtime)
    chat_a,chat_b=store.create_chat(uid(),'A'),store.create_chat(uid(),'B')
    a=service.generate(uid(),chat_a['id'],'a',1)
    assert first_ready.wait(3)
    stopping=threading.Thread(target=lambda:service.stop(uid(),a['id']))
    stopping.start(); assert cancel_enter.wait(3)
    first_finish.set()
    until(lambda:store.generation(a['id'])['state'] in {'stopped','failed','completed'})
    second,errors=[],[]
    op=uid()
    def submit_b():
        try: second.append(service.generate(op,chat_b['id'],'b',1))
        except CoreFailure as exc: errors.append(exc.code)
    starting=threading.Thread(target=submit_b)
    starting.start()
    second_ready.wait(.1)  # Old implementation can accept B while A's stop is paused.
    cancel_release.set(); stopping.join(3); starting.join(3)
    if not second:
        assert errors==['CORE_BUSY']
        until(lambda:service.status()['active_generation'] is None)
        second.append(service.generate(op,chat_b['id'],'b',1))
    assert second_ready.wait(3)
    second_finish.set()
    try:
        until(lambda:store.generation(second[0]['id'])['state'] in {'completed','stopped','failed'})
        assert store.generation(second[0]['id'])['state']=='completed'
    finally:
        first_finish.set(); cancel_release.set(); second_finish.set()
        service.close(); store.close()


def test_stop_during_preload_does_not_wait_or_cancel_the_model_owner(tmp_path,monkeypatch):
    model=tmp_path/'fixture.gguf'; model.write_bytes(b'GGUFtest')
    runtime=LlamaRuntime(RuntimeConfig(Path(sys.executable),model,choose_loopback_port(),startup_timeout=2.))
    fixture=Path(__file__).parents[2]/'tests/fixtures/fake_llama.py'
    monkeypatch.setattr(runtime,'_binary_command',lambda:[getattr(sys,'_base_executable',sys.executable),str(fixture)])
    entered,resume=threading.Event(),threading.Event()
    original=runtime.start
    def delayed_start():
        entered.set(); assert resume.wait(3); original()
    monkeypatch.setattr(runtime,'start',delayed_start)
    store=MainStore(tmp_path/'main.db',secrets.token_bytes(32)); service=CoreService(store,runtime=runtime)
    chat=store.create_chat(uid(),'handoff')
    generation=service.generate(uid(),chat['id'],'input',1)
    try:
        assert entered.wait(3)
        service.stop(uid(),generation['id'])
        before=time.monotonic()
        until(lambda:store.generation(generation['id'])['state']=='stopped',seconds=.9)
        assert time.monotonic()-before<.9
        resume.set()
        until(lambda:service.status()['runtime_state']=='READY')
        assert runtime.process is not None and runtime.process.poll() is None
    finally: resume.set(); service.close(); store.close()
