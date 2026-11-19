"""Core continuation flows through async worker and preserves existing message."""
import sqlite3
import time
from uuid import uuid4
import pytest
import server.ego_core.store as store_module
from server.ego_core.store import MainStore
from server.ego_core.service import CoreService
from runtime.adapters.llama_cpp import LlamaRuntime, StreamEvent

@pytest.fixture(autouse=True)
def sqlite_for_contract(monkeypatch):
    monkeypatch.setattr(store_module, '_ThreadSafeDriver', lambda:None)
    monkeypatch.setattr(store_module, 'open_database',lambda path,key,driver=None:sqlite3.connect(str(path),check_same_thread=False))

def uid(): return str(uuid4())

class Runtime(LlamaRuntime):
    def __init__(self):
        self.config=type('Cfg',(),{'context':4096,'startup_timeout':5})()
        self.received=[]
    def stream_chat(self,messages,max_tokens=None):
        self.received.append(messages)
        yield StreamEvent(' and more')
        yield StreamEvent('',stop=True,finish_reason='stop',timings={'prompt_n':64,'predicted_n':3,'predicted_ms':100})

def test_continuation_core_api_worker(tmp_path):
    store=MainStore(tmp_path/'main.db',bytes(range(32)))
    chat=store.create_chat(uid(),'scene')
    gen=store.begin_generation(uid(),chat['id'],'Describe a scene',chat['version'])
    store.set_generation_state(gen['id'],'started')
    store.set_generation_state(gen['id'],'completed',text='First part')
    runtime=Runtime()
    svc=CoreService(store,runtime=runtime)
    with svc._condition: svc._transition('READY')
    version=store.list_chats()[0]['version']
    receipt=svc.continue_generation(uid(),chat['id'],version)
    deadline=time.monotonic()+5
    while store.generation(receipt['id'])['state'] not in ('completed','failed','stopped') and time.monotonic()<deadline:
        time.sleep(.01)
    assert store.generation(receipt['id'])['state']=='completed'
    assert [x['content'] for x in store.messages(chat['id'])]==['Describe a scene','First part and more']
    assert runtime.received[0][-1]['role']=='user'
    assert 'First part' in runtime.received[0][-2]['content']
    store.close()
