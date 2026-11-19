"""M1.7 independent-client SSE snapshot resume contract."""
import asyncio
import hashlib
import json
import secrets
import httpx
from server.ego_core.api import create_app

GEN='00000000-0000-4000-8000-000000000001'
class Store:
    def generation(self, key):
        assert key == GEN
        return {'id':GEN,'state':'completed','text':'Hello'}
class Service:
    store=Store()
    installation_id=None
    last_api_error=None

async def fetch(headers):
    app=create_app(Service(),TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
        return await client.get('/v1/generations/'+GEN+'/events',headers=headers)
TOKEN=secrets.token_urlsafe(32)

def test_sse_has_deterministic_id_and_resumes_without_duplicate():
    auth={'Authorization':'Bearer '+TOKEN}
    response=asyncio.run(fetch(auth))
    assert response.status_code==200
    expected=json.dumps(Service.store.generation(GEN),ensure_ascii=True,separators=(',',':'))
    digest=hashlib.sha256(expected.encode('utf-8')).hexdigest()
    assert f'id: {digest}\n' in response.text
    assert 'event: generation\n' in response.text
    resumed=asyncio.run(fetch({**auth,'Last-Event-ID':digest}))
    assert resumed.status_code==200
    assert 'event: generation\n' not in resumed.text

def test_sse_rejects_invalid_cursor():
    resp=asyncio.run(fetch({'Authorization':'Bearer '+TOKEN,'Last-Event-ID':'oops'}))
    assert resp.status_code==422

def test_sse_unauthenticated_is_rejected():
    resp=asyncio.run(fetch({}))
    assert resp.status_code==401
