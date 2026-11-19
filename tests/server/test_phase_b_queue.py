"""Pure scheduling tests; does not use production SQLCipher data."""
import threading
import time
from uuid import uuid4
from server.ego_core.service import CoreService

class FakeStore:
    def __init__(self):
        self.rows={}
        self.stopped=[]
    def recover_interrupted(self): return 0
    def generation(self, gid): return dict(self.rows[gid])
    def set_generation_state(self,gid,state,**kwargs):
        self.rows[gid]['state']=state
        return self.generation(gid)
    def request_stop(self,oid,gid):
        self.rows[gid]['stop_requested']=True
        return self.generation(gid)

class Runtime:
    def __init__(self): self.config=type('Config',(),{'context':2048})()


def test_queue_starts_first_then_next_and_can_cancel_pending():
    store=FakeStore(); service=CoreService(store,runtime=Runtime())
    service._runtime_state='READY'
    started=[]; release=threading.Event()
    def fake_run(row,stop,name,desc):
        started.append(row['id'])
        if len(started)==1: release.wait(3)
        store.set_generation_state(row['id'],'completed')
        with service._condition:
            service._active=None
            service._start_next_locked()
    service._run=fake_run
    ids=[str(uuid4()) for _ in range(3)]
    with service._condition:
        for gid in ids:
            row={'id':gid,'state':'queued','stop_requested':False}
            store.rows[gid]=dict(row)
            service._pending.append((row,None,None))
        service._start_next_locked()
    deadline=time.time()+3
    while not started and time.time()<deadline: time.sleep(.01)
    assert started==ids[:1]
    assert service.status()['queued_generations']==ids[1:]
    service.stop(str(uuid4()),ids[1])
    assert store.generation(ids[1])['state']=='stopped'
    release.set()
    deadline=time.time()+3
    while store.generation(ids[2])['state']!='completed' and time.time()<deadline:time.sleep(.01)
    assert started==[ids[0],ids[2]]
    assert not service.status()['queued_generations']
