import asyncio
import httpx
from server.ego_core.api import create_app

class FakeService:
    installation_id = None
    last_api_error = None
    def model_info(self): return {'state':'READY','name':'test.gguf','ready':True}
    def status(self): return {'state':'READY','last_api_error':self.last_api_error}

def test_browser_401_does_not_poison_core_status():
    service=FakeService()
    app=create_app(service,'A'*43)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as c:
            assert (await c.get('/v1/runtime/model')).status_code==401
            res=await c.get('/v1/system/status',headers={'authorization':'Bearer '+'A'*43})
            assert res.status_code==200
            assert res.json()['last_api_error'] is None
    asyncio.run(run())
