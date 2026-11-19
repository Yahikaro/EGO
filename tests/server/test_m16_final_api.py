"""New continuation endpoint uses the same local authorization as generation."""
import asyncio
import secrets
from uuid import uuid4
import httpx
from server.ego_core.api import create_app

class Service:
    installation_id=None
    last_api_error=None
    def __init__(self): self.calls=[]
    def continue_generation(self,**kwargs):
        self.calls.append(kwargs)
        return {'id':'fake','state':'queued'}

async def post(app, token, headers):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as cli:
        return await cli.post('/v1/generations/continue',headers=headers,json={
            'operation_id':str(uuid4()),'chat_id':str(uuid4()),'expected_version':3,
            'character_name':None,'character_description':None})

def test_continuation_requires_auth_and_accepts_structured_payload():
    service=Service(); token=secrets.token_urlsafe(32); app=create_app(service,token)
    bad=asyncio.run(post(app,token,{}))
    assert bad.status_code==401 and not service.calls
    good=asyncio.run(post(app,token,{'Authorization':'Bearer '+token}))
    assert good.status_code==202
    assert good.json()['generation']['state']=='queued'
    assert service.calls[0]['expected_version']==3
