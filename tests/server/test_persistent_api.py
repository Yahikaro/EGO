"""Authenticated runtime configuration boundary; persistence is GUI/backend-owned."""
import secrets
from pathlib import Path
from server.ego_core.api import create_app
from server.ego_core.service import CoreService
from server.ego_core.config import CoreConfig
from server.ego_core.store import MainStore
from runtime.adapters.llama_cpp import RuntimeConfig,StreamEvent
from test_core_api import LocalClient
from test_persistent_service import wait_for


class LocalRuntime:
    def __init__(self,config): self.config=config;self.started=0;self.closed=False
    def start(self): self.started+=1;self.closed=False
    def stream(self,prompt): yield StreamEvent('local',True)
    def cancel_generation(self): pass
    def cancel(self): self.shutdown()
    def shutdown(self): self.closed=True


def test_owner_header_and_runtime_restart_are_authenticated(tmp_path):
    store=MainStore(tmp_path/'main.db',secrets.token_bytes(32))
    service=CoreService(store,runtime=LocalRuntime(RuntimeConfig(Path('binary'),Path('model'))))
    service.installation_id='installation-A'
    token=secrets.token_urlsafe(32);client=LocalClient(create_app(service,token))
    try:
        assert client.post('/v1/runtime/restart',json={}).status_code==401
        client.headers['authorization']='Bearer '+token
        assert client.post('/v1/runtime/restart',json={}).status_code==403
        client.headers['X-EGO-Installation']='installation-B'
        assert client.post('/v1/runtime/restart',json={}).json()['code']=='CORE_INSTALLATION_MISMATCH'
        client.headers['X-EGO-Installation']='installation-A'
        response=client.post('/v1/runtime/restart',json={})
        assert response.status_code==202
        wait_for(lambda:service.status()['runtime_state']=='READY')
        assert service.runtime.started==1
    finally:service.close();store.close()


def test_request_options_keep_loaded_instance_loading_changes_replace_it(tmp_path):
    model=tmp_path/'model.gguf';model.write_bytes(b'GGUFfixture')
    binary=tmp_path/'binary';binary.write_bytes(b'local fixture')
    config=CoreConfig(binary=str(binary),model=str(model))
    store=MainStore(tmp_path/'main.db',secrets.token_bytes(32))
    first=LocalRuntime(config.runtime_config());service=CoreService(store,runtime=first)
    token=secrets.token_urlsafe(32);client=LocalClient(create_app(service,token));client.headers['authorization']='Bearer '+token
    try:
        service.start_runtime();wait_for(lambda:service.status()['runtime_state']=='READY')
        values={**config.model_dump(),'temperature':.8,'max_tokens':256}
        response=client.post('/v1/runtime/configure',json=values)
        assert response.status_code==202
        assert service.runtime is first and first.started==1
        assert first.config.temperature==.8 and first.config.max_tokens==256
        values['context']=4096
        response=client.post('/v1/runtime/configure',json=values)
        assert response.status_code==202
        wait_for(lambda:service.status()['runtime_state']=='READY')
        assert service.runtime is not first and first.closed
        assert service.runtime.config.context==4096
        assert not (tmp_path/'data/config.json').exists()
    finally:service.close();store.close()
