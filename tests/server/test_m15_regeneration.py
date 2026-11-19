"""M1.5: regenerative replacement preserves original until successful completion."""
from uuid import uuid4
import pytest
import sqlite3
import server.ego_core.store as store_module
from server.ego_core.store import MainStore, StoreFailure

@pytest.fixture(autouse=True)
def synthetic_sqlite_store(monkeypatch):
    # SQL semantics only; genuine SQLCipher verification runs on Windows.
    monkeypatch.setattr(store_module, "_ThreadSafeDriver", lambda: None)
    monkeypatch.setattr(store_module, "open_database", lambda path,key,driver=None: sqlite3.connect(str(path),check_same_thread=False))


def uid(): return str(uuid4())

def setup(tmp_path):
    store=MainStore(tmp_path/'regen.db',bytes(range(32)))
    chat=store.create_chat(uid(),'Regen')
    g=store.begin_generation(uid(),chat['id'],'Привет',chat['version'])
    store.set_generation_state(g['id'],'started')
    store.set_generation_state(g['id'],'completed',text='Старый ответ')
    return store, chat['id']

def current_version(store,cid):
    return next(x['version'] for x in store.list_chats() if x['id']==cid)

def test_regenerate_keeps_old_answer_until_success_then_replaces(tmp_path):
    store,cid=setup(tmp_path)
    gen=store.begin_regeneration(uid(),cid,current_version(store,cid))
    assert [x['content'] for x in store.messages(cid)]==['Привет','Старый ответ']
    assert [x['content'] for x in store.generation_context(gen['id'])]==['Привет']
    store.set_generation_state(gen['id'],'started')
    store.set_generation_state(gen['id'],'completed',text='Новый ответ')
    assert [x['content'] for x in store.messages(cid)]==['Привет','Новый ответ']
    store.close()

def test_failed_regeneration_keeps_old_answer(tmp_path):
    store,cid=setup(tmp_path)
    gen=store.begin_regeneration(uid(),cid,current_version(store,cid))
    store.set_generation_state(gen['id'],'started')
    store.set_generation_state(gen['id'],'failed',text='partial',error_code='RUNTIME_INTERNAL_ERROR')
    assert [x['content'] for x in store.messages(cid)]==['Привет','Старый ответ']
    store.close()

def test_regenerate_rejects_stale_version(tmp_path):
    store,cid=setup(tmp_path)
    with pytest.raises(StoreFailure,match='VERSION_CONFLICT'):
        store.begin_regeneration(uid(),cid,1)
    store.close()
