"""M1 vertical checks use real SQLCipher and disposable runtime worker."""
import importlib
import json
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path
import pytest
import asyncio
import httpx
from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeConfig
from runtime.adapters.ports import choose_loopback_port


class LocalClient:
    # Exercise the installed ASGI transport directly; no deprecated TestClient shim.
    def __init__(self, app): self.app, self.headers = app, {}
    def request(self, method, path, **kwargs):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                         base_url='https://localhost', headers=self.headers) as c:
                return await c.request(method,path,**kwargs)
        return asyncio.run(run())
    def get(self,path,**kwargs): return self.request('GET',path,**kwargs)
    def post(self,path,**kwargs): return self.request('POST',path,**kwargs)
    def put(self,path,**kwargs): return self.request('PUT',path,**kwargs)


def uid(): return str(uuid.uuid4())


@pytest.fixture
def core(tmp_path, monkeypatch):
    root=Path(__file__).parents[2]/'server/ego_core'
    assert (root/'service.py').is_file() and (root/'api.py').is_file(), 'M1 core API missing'
    from server.ego_core.store import MainStore
    from server.ego_core.service import CoreService
    from server.ego_core.api import create_app
    model=tmp_path/'fixture.gguf'; model.write_bytes(b'GGUFtest')
    runtime=LlamaRuntime(RuntimeConfig(Path(sys.executable),model,choose_loopback_port(),startup_timeout=5))
    fixture=Path(__file__).parents[2]/'tests/fixtures/fake_llama.py'
    monkeypatch.setattr(runtime,'_binary_command',lambda:[getattr(sys,'_base_executable',sys.executable),str(fixture)])
    store=MainStore(tmp_path/'main.db',secrets.token_bytes(32))
    service=CoreService(store,runtime=runtime)
    token=secrets.token_urlsafe(32)
    app=create_app(service,token)
    client=LocalClient(app)
    client.headers['authorization']='Bearer '+token
    try:
        yield client,service,store,runtime,token
    finally:
        service.close()
    service.close()
    store.close()


def new_chat(client,title='test'):
    response=client.post('/v1/chats',json={'operation_id':uid(),'title':title})
    assert response.status_code==200,response.text
    return response.json()


def wait_terminal(client,generation_id):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        response=client.get('/v1/generations/'+generation_id)
        assert response.status_code==200,response.text
        result=response.json()
        if result['state'] in {'completed','stopped','failed'}: return result
        time.sleep(.02)
    pytest.fail('generation did not reach terminal state')


def test_health_is_public_but_all_data_and_schema_require_auth(core):
    c,*_=core
    assert c.get('/health',headers={'authorization':''}).json()['status']=='ok'
    for url in ['/v1/system/status','/v1/chats','/v1/events','/v1/openapi.json']:
        response=c.get(url,headers={'authorization':'Bearer wrong'})
        assert response.status_code==401
        assert response.json()['code']=='UNAUTHORIZED' and set(response.json())<={'code','message','hint'}


def test_chats_and_generations_are_durable_idempotent_and_isolated(core):
    c,service,store,runtime,_=core
    a,b=new_chat(c,'A'),new_chat(c,'B')
    body={'operation_id':uid(),'chat_id':a['id'],'content':'synthetic private A','expected_version':a['version']}
    result=c.post('/v1/generations',json=body)
    assert result.status_code==202,result.text
    gen=result.json()['generation']
    assert result.json()['ack']=='persisted'
    final=wait_terminal(c,gen['id'])
    assert final['state']=='completed' and final['text']=='xxx'
    assert c.post('/v1/generations',json=body).json()['generation']['id']==gen['id']
    assert c.get('/v1/chats/'+b['id']+'/messages').json()==[]
    messages=c.get('/v1/chats/'+a['id']+'/messages').json()
    assert [m['role'] for m in messages]==['user','assistant']
    assert len(messages)==2 and messages[1]['content']=='xxx'
    assert runtime.process is not None and runtime.process.poll() is None
    body['content']='different'
    assert c.post('/v1/generations',json=body).json()['code']=='IDEMPOTENCY_CONFLICT'
    assert c.get('/v1/operations/'+body['operation_id']).json()['status']=='completed'
    assert c.get('/v1/events').json()['events']


def test_strict_errors_never_echo_private_input_and_body_size_is_bounded(core):
    c,*_=core
    secret='EGO_SECRET_INVALID_INPUT'
    result=c.post('/v1/chats',json={'operation_id':secret,'title':secret,'extra':secret})
    assert result.status_code==422 and secret not in result.text
    result=c.post('/v1/chats',content=b'x'*70000,headers={'content-type':'application/json'})
    assert result.status_code==413 and result.json()['code']=='REQUEST_TOO_LARGE' and secret not in result.text


def test_same_operation_replay_does_not_start_second_worker(core,monkeypatch):
    c,service,store,runtime,_=core
    count=[]; start=runtime.start
    def counted(): count.append(True); return start()
    monkeypatch.setattr(runtime,'start',counted)
    chat=new_chat(c)
    body={'operation_id':uid(),'chat_id':chat['id'],'content':'test','expected_version':1}
    one=c.post('/v1/generations',json=body).json()['generation']
    two=c.post('/v1/generations',json=body).json()['generation']
    assert one['id']==two['id']
    assert wait_terminal(c,one['id'])['state']=='completed'
    assert len(count)==1


def test_stop_saves_only_marked_partial_and_allows_next_generation(core,monkeypatch):
    c,service,store,runtime,_=core
    seen,resume=threading.Event(),threading.Event()
    original=runtime.stream_chat
    def paused(*args,**kwargs):
        for event in original(*args,**kwargs):
            yield event
            seen.set()
            assert resume.wait(3)
    monkeypatch.setattr(runtime,'stream_chat',paused)
    chat=new_chat(c)
    body={'operation_id':uid(),'chat_id':chat['id'],'content':'test','expected_version':1}
    gen=c.post('/v1/generations',json=body).json()['generation']
    try:
        assert seen.wait(3)
        op={'operation_id':uid()}
        response=c.post('/v1/generations/'+gen['id']+'/stop',json=op)
        assert response.status_code==202
        assert response.json()['stop_requested']
    finally: resume.set()
    final=wait_terminal(c,gen['id'])
    assert final['state']=='stopped' and final['text']=='x'
    messages=c.get('/v1/chats/'+chat['id']+'/messages').json()
    assert messages[-1]['status']=='stopped'
    assert runtime.process is not None and runtime.process.poll() is None


def test_stream_snapshot_reconnect_returns_same_final_generation(core):
    c,*_=core
    chat=new_chat(c)
    gen=c.post('/v1/generations',json={'operation_id':uid(),'chat_id':chat['id'],'content':'test','expected_version':1}).json()['generation']
    first=c.get('/v1/generations/'+gen['id']+'/events')
    assert first.status_code==200 and 'text/event-stream' in first.headers['content-type']
    snapshots=[json.loads(line[6:]) for line in first.text.splitlines() if line.startswith('data: ')]
    assert snapshots[-1]['state']=='completed' and snapshots[-1]['text']=='xxx'
    second=c.get('/v1/generations/'+gen['id']+'/events')
    assert json.loads(next(line[6:] for line in second.text.splitlines() if line.startswith('data: ')))['id']==gen['id']


def test_worker_failure_is_recorded_without_exception_text(core,monkeypatch):
    c,_,_,runtime,_=core
    def fail(): raise OSError('private OS path and key')
    monkeypatch.setattr(runtime,'start',fail)
    chat=new_chat(c)
    gen=c.post('/v1/generations',json={'operation_id':uid(),'chat_id':chat['id'],'content':'test','expected_version':1}).json()['generation']
    final=wait_terminal(c,gen['id'])
    assert final['state']=='failed' and final['error_code']=='RUNTIME_INTERNAL_ERROR'
    assert 'private OS' not in json.dumps(final)


def test_unconfigured_runtime_does_not_accept_or_store_generation(core):
    c,service,store,_,_=core
    service.runtime=None
    chat=new_chat(c)
    result=c.post('/v1/generations',json={'operation_id':uid(),'chat_id':chat['id'],'content':'test','expected_version':1})
    assert result.status_code==409 and result.json()['code']=='MODEL_NOT_PROVIDED'
    assert store.messages(chat['id'])==[]


def test_chunked_body_is_bounded_before_json_parser(core):
    c,*_=core
    async def run():
        async def chunks():
            for _ in range(20): yield b'x'*4096
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app),base_url='https://localhost',headers=c.headers) as client:
            return await client.post('/v1/chats',content=chunks(),headers={'content-type':'application/json'})
    result=asyncio.run(run())
    assert result.status_code==413
    assert result.json()['code']=='REQUEST_TOO_LARGE' and set(result.json())<={'code','message','hint'}


def test_real_worker_context_and_prompt_do_not_cross_chat_boundaries(core,monkeypatch):
    c,service,store,runtime,_=core
    prompts,workers=[],[]
    original=runtime.stream_chat
    def capture(messages,*args,**kwargs):
        prompts.append(json.dumps(messages,ensure_ascii=False)); workers.append(runtime.process)
        return original(messages,*args,**kwargs)
    monkeypatch.setattr(runtime,'stream_chat',capture)
    for marker in ['CHAT_A_PRIVATE_CANARY','CHAT_B_PRIVATE_CANARY']:
        chat=new_chat(c)
        generation=c.post('/v1/generations',json={'operation_id':uid(),'chat_id':chat['id'],'content':marker,'expected_version':1}).json()['generation']
        assert wait_terminal(c,generation['id'])['state']=='completed'
    assert 'CHAT_A_PRIVATE_CANARY' in prompts[0]
    assert 'CHAT_A_PRIVATE_CANARY' not in prompts[1]
    assert 'CHAT_B_PRIVATE_CANARY' in prompts[1]
    assert workers[0] is workers[1] and workers[0].poll() is None
    service.close()
    assert workers[0].poll() is not None


def test_unexpected_api_failure_is_structured_without_private_exception_details(core,monkeypatch):
    c,_,store,_,_=core
    def fail(): raise RuntimeError('private content from dependency')
    monkeypatch.setattr(store,'list_chats',fail)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app,raise_app_exceptions=False),base_url='https://localhost',headers=c.headers) as client:
            return await client.get('/v1/chats')
    result=asyncio.run(run())
    assert result.status_code==500
    assert result.json()['code']=='INTERNAL_ERROR' and 'private content' not in result.text


def test_api_error_audit_receives_only_fixed_codes(core):
    from server.ego_core.api import create_app
    c,service,_,_,token=core
    errors=[]
    audited=LocalClient(create_app(service,token,error_callback=errors.append))
    assert audited.get('/v1/chats').status_code==401
    audited.headers['authorization']='Bearer '+token
    assert audited.post('/v1/chats',json={'private':'DO_NOT_AUDIT_THIS'}).status_code==422
    # Since M1.6 an unauthenticated probe is deliberately not audited, so it
    # cannot replace the last actionable Core error.
    assert errors==['INVALID_REQUEST']


def test_browser_unauthorized_does_not_poison_core_health(core):
    client, service, *_ = core
    assert client.get('/v1/runtime/model',headers={'authorization':''}).status_code == 401
    assert service.last_api_error is None
    assert client.get('/v1/system/status').status_code == 200
    assert client.get('/v1/system/status').json()['last_api_error'] is None
