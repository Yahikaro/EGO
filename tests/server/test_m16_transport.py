"""M1.6 API contract tests without platform-specific model startup."""
import asyncio
import secrets
import httpx
from server.ego_core.api import create_app

class MemoryStore:
    def __init__(self, state='completed'): self.state=state
    def generation(self, key):
        if key!='g-valid': raise ValueError('unexpected test key')
        return {'id':key,'state':self.state,'text':'hello'}

class FakeService:
    def __init__(self,state='completed'):
        self.store=MemoryStore(state)
        self.installation_id=None
        self.runtime=None
        self.last_api_error=None
        self._runtime_state='STOPPED'
    def model_info(self):
        return {'state':'STOPPED','name':None,'configured':False,'ready':False,'busy':False,'context':None,'max_tokens':None}

async def request(app,url,token):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as c:
        return await c.get(url,headers={'authorization':'Bearer '+token})

def test_model_endpoint_requires_auth_and_returns_public_metadata():
    token=secrets.token_urlsafe(32);app=create_app(FakeService(),token)
    unauthorized=asyncio.run(request(app,'/v1/runtime/model','wrong'))
    assert unauthorized.status_code==401
    good=asyncio.run(request(app,'/v1/runtime/model',token))
    assert good.status_code==200
    assert good.json()['configured'] is False

def test_terminal_sse_event_is_delivered_without_blocking():
    token=secrets.token_urlsafe(32);app=create_app(FakeService(),token)
    response=asyncio.run(request(app,'/v1/generations/g-valid/events',token))
    assert response.status_code==200
    assert 'event: generation' in response.text
    assert '"state":"completed"' in response.text
    assert response.headers['cache-control']=='no-store'
    assert response.headers['x-accel-buffering']=='no'

def test_service_model_metadata_exposes_name_but_never_absolute_path(tmp_path):
    from pathlib import Path
    from server.ego_core.service import CoreService
    class Store:
        def recover_interrupted(self): return 0
    class Config:
        model=tmp_path/'private-model.gguf'
        context=4096
        max_tokens=512
    class Runtime:
        config=Config()
        process=None
    service=CoreService(Store(),runtime=Runtime())
    result=service.model_info()
    assert result['name']=='private-model.gguf'
    assert result['context']==4096 and result['max_tokens']==512
    assert result['configured'] is True and result['ready'] is False
    assert str(tmp_path) not in str(result)
