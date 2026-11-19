"""Continuation is atomic and does not lose previous message on stop/failure."""
from uuid import uuid4
import sqlite3
import pytest
import server.ego_core.store as module
from server.ego_core.store import MainStore

@pytest.fixture(autouse=True)
def sqlite_test_driver(monkeypatch):
    monkeypatch.setattr(module,'_ThreadSafeDriver',lambda:None)
    monkeypatch.setattr(module,'open_database',lambda path,key,driver=None:sqlite3.connect(str(path),check_same_thread=False))

def uid(): return str(uuid4())

def baseline(tmp_path):
    store=MainStore(tmp_path/'test.sqlite',bytes(range(32)))
    chat=store.create_chat(uid(),'Continued')
    gen=store.begin_generation(uid(),chat['id'],'Write scene',chat['version'])
    store.set_generation_state(gen['id'],'started')
    store.set_generation_state(gen['id'],'completed',text='First half')
    return store,chat['id']

def version(store,cid):return next(r['version'] for r in store.list_chats() if r['id']==cid)

def test_successful_continuation_appends_to_same_message(tmp_path):
    store,cid=baseline(tmp_path)
    first=store.messages(cid)[-1]
    gen=store.begin_continuation(uid(),cid,version(store,cid))
    assert store.messages(cid)[-1]['content']=='First half'
    store.set_generation_state(gen['id'],'started')
    store.set_generation_state(gen['id'],'completed',text=' second half')
    last=store.messages(cid)[-1]
    assert last['content']=='First half second half'
    assert last['id']==first['id']
    assert len(store.messages(cid))==2
    store.close()

@pytest.mark.parametrize('terminal', ['stopped','failed'])
def test_cancel_or_failure_preserves_original(tmp_path,terminal):
    store,cid=baseline(tmp_path)
    gen=store.begin_continuation(uid(),cid,version(store,cid))
    store.set_generation_state(gen['id'],'started')
    store.set_generation_state(gen['id'],terminal,text='a partial' if terminal=='stopped' else '',error_code='RUNTIME_INTERNAL_ERROR' if terminal=='failed' else None)
    assert [x['content'] for x in store.messages(cid)]==['Write scene','First half']
    store.close()

def test_continuation_removes_repeated_boundary_at_sentence_scale(tmp_path):
    store,cid=baseline(tmp_path)
    # Existing test story shorter; reproduce a natural repeated fragment.
    tail=' The silver door opened slowly.'
    gen=store.begin_continuation(uid(),cid,version(store,cid))
    store.set_generation_state(gen['id'],'started')
    store.set_generation_state(gen['id'],'completed',text='First half' + tail)
    assert store.messages(cid)[-1]['content']=='First half' + tail
    store.close()
