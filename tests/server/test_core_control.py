import importlib
import os
from pathlib import Path
import secrets
import sys
import subprocess
import uuid
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from server.ego_core.keys import KeyVault


@pytest.fixture
def vault_factory():
    cipher=AESGCM(secrets.token_bytes(32))
    def protect(data):
        nonce=secrets.token_bytes(12)
        return nonce+cipher.encrypt(nonce,data,None)
    def unprotect(data): return cipher.decrypt(data[:12],data[12:],None)
    return lambda directory: KeyVault(directory,protect=protect,unprotect=unprotect)


def control():
    assert (Path(__file__).parents[2]/'server/ego_core/control.py').is_file(), 'M1 control missing'
    return importlib.import_module('server.ego_core.control')


def test_initialization_and_reopen_keep_both_encrypted_domains_and_identity(tmp_path,vault_factory):
    m=control()
    assert m.initialize(tmp_path,vault_factory=vault_factory)['code']=='CORE_INITIALIZED'
    before={p.name:p.read_bytes() for p in (tmp_path/'data').iterdir() if p.suffix=='.protected'}
    core=m.assemble(tmp_path,vault_factory=vault_factory)
    assert core.service.status()['runtime']=='unconfigured'
    core.close()
    assert m.initialize(tmp_path,vault_factory=vault_factory)['code']=='CORE_INITIALIZED'
    assert before=={p.name:p.read_bytes() for p in (tmp_path/'data').iterdir() if p.suffix=='.protected'}
    for name in ['main.db','profile.db']:
        assert not (tmp_path/'data'/name).read_bytes().startswith(b'SQLite format 3')


def test_missing_vault_does_not_replace_keys_beside_existing_data(tmp_path,vault_factory):
    m=control(); m.initialize(tmp_path,vault_factory=vault_factory)
    identity=tmp_path/'data/keys.protected'; identity.unlink()
    before=(tmp_path/'data/main.db').read_bytes()
    with pytest.raises(Exception): m.initialize(tmp_path,vault_factory=vault_factory)
    assert not identity.exists() and (tmp_path/'data/main.db').read_bytes()==before


def test_cli_production_on_linux_blocks_without_plaintext_key_fallback(tmp_path):
    if os.name=='nt': pytest.skip('Non-Windows fail-closed production check')
    module=control()
    result=subprocess.run([sys.executable,'-m','server.ego_core.control','setup','--root',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode!=0 and 'WINDOWS_DPAPI_REQUIRED' in result.stdout
    assert not (tmp_path/'data/keys.protected').exists()
    assert list((tmp_path/'logs').glob('*core-control.log'))
    report=(tmp_path/'logs/latest-core-report.json').read_text()
    assert 'WINDOWS_DPAPI_REQUIRED' in report


def test_client_never_adopts_foreign_or_untrusted_endpoint(tmp_path,vault_factory,monkeypatch):
    import httpx
    m=control(); m.initialize(tmp_path,vault_factory=vault_factory)
    from server.ego_core.config import load_config,save_config,CoreConfig
    save_config(tmp_path/'data/config.json', CoreConfig(local_http=False))
    def refused(*args,**kwargs): raise httpx.ConnectError('private endpoint details')
    monkeypatch.setattr(httpx.Client,'request',refused)
    with pytest.raises(m.ControlFailure,match='CORE_OFFLINE'):
        m.request(tmp_path,'GET','/v1/system/status',vault_factory=vault_factory)
    def wrong_cert(*args,**kwargs): raise httpx.ConnectError('[SSL: CERTIFICATE_VERIFY_FAILED] private')
    monkeypatch.setattr(httpx.Client,'request',wrong_cert)
    with pytest.raises(m.ControlFailure,match='CORE_TLS_FAILED'):
        m.request(tmp_path,'GET','/v1/system/status',vault_factory=vault_factory)


def test_real_https_core_fixture_generation_and_authenticated_shutdown(tmp_path,vault_factory,monkeypatch):
    import logging
    import threading
    import time
    import ssl
    import httpx
    from server.ego_core.config import CoreConfig,save_config
    from runtime.adapters.llama_cpp import LlamaRuntime
    from runtime.adapters.ports import choose_loopback_port
    m=control(); m.initialize(tmp_path,vault_factory=vault_factory)
    model=tmp_path/'fixture.gguf'; model.write_bytes(b'GGUFtest')
    port=choose_loopback_port(); runtime_port=choose_loopback_port()
    while runtime_port==port: runtime_port=choose_loopback_port()
    save_config(tmp_path/'data/config.json',CoreConfig(binary=sys.executable,model=str(model),api_port=port,runtime_port=runtime_port,startup_timeout=5.,local_http=False))
    fixture=Path(__file__).parents[2]/'tests/fixtures/fake_llama.py'
    def runtime_factory(config):
        runtime=LlamaRuntime(config)
        monkeypatch.setattr(runtime,'_binary_command',lambda:[getattr(sys,'_base_executable',sys.executable),str(fixture)])
        return runtime
    errors=[]
    def serve():
        try: m.serve(tmp_path,vault_factory=vault_factory,runtime_factory=runtime_factory)
        except BaseException as exc: errors.append(exc)
    before=logging.root.manager.disable
    thread=threading.Thread(target=serve,daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+5
        while True:
            try:
                status=m.request(tmp_path,'GET','/v1/system/status',vault_factory=vault_factory,timeout=.5)
                break
            except m.ControlFailure:
                if time.monotonic()>deadline: pytest.fail('HTTPS core did not become ready: '+repr(errors))
                time.sleep(.03)
        assert status['storage']=='ready' and status['runtime']=='idle'
        context=ssl.create_default_context(cafile=str(tmp_path/'data/identity.crt.pem'))
        with httpx.Client(verify=context,trust_env=False) as client:
            assert client.get(f'https://127.0.0.1:{port}/v1/chats').status_code==401
        chat=m.request(tmp_path,'POST','/v1/chats',payload={'operation_id':str(uuid.uuid4()),'title':'TLS fixture'},vault_factory=vault_factory)
        generation=m.request(tmp_path,'POST','/v1/generations',payload={'operation_id':str(uuid.uuid4()),'chat_id':chat['id'],'content':'synthetic','expected_version':1},vault_factory=vault_factory)['generation']
        deadline=time.monotonic()+5
        while True:
            result=m.request(tmp_path,'GET','/v1/generations/'+generation['id'],vault_factory=vault_factory)
            if result['state'] in {'completed','stopped','failed'}: break
            assert time.monotonic()<deadline
            time.sleep(.02)
        assert result['state']=='completed' and result['text']=='xxx'
    finally:
        try: m.request(tmp_path,'POST','/v1/system/shutdown',payload={'operation_id':str(uuid.uuid4())},vault_factory=vault_factory)
        except m.ControlFailure: pass
        thread.join(5)
        logging.disable(before)
    assert not thread.is_alive() and not errors


def test_foreign_listener_start_failure_never_recovers_or_mutates_store(tmp_path,vault_factory):
    import socket
    from server.ego_core.config import CoreConfig,save_config
    from server.ego_core.store import MainStore
    m=control(); m.initialize(tmp_path,vault_factory=vault_factory)
    identity=vault_factory(tmp_path/'data').unlock()
    store=MainStore(tmp_path/'data/main.db',identity.secrets.main_key)
    chat=store.create_chat(str(uuid.uuid4()),'other running owner')
    generation=store.begin_generation(str(uuid.uuid4()),chat['id'],'persisted pending',1)
    with socket.socket() as foreign:
        foreign.bind(('127.0.0.1',0)); foreign.listen()
        save_config(tmp_path/'data/config.json',CoreConfig(api_port=foreign.getsockname()[1]))
        with pytest.raises(OSError): m.serve(tmp_path,vault_factory=vault_factory)
    assert store.generation(generation['id'])['state']=='queued'
    store.close()


def test_data_lease_rejects_second_core_until_owner_releases(tmp_path):
    m=control()
    assert hasattr(m,'CoreLease'), 'single owner data lease missing'
    with m.CoreLease(tmp_path):
        with pytest.raises(m.ControlFailure,match='CORE_ALREADY_RUNNING'):
            with m.CoreLease(tmp_path): pass
    with m.CoreLease(tmp_path): pass


def test_child_failure_diagnostics_keep_only_code_and_numeric_os_errors(tmp_path):
    import json
    m=control()
    assert hasattr(m,'child_failure'),'owned child failure parser missing'
    path=tmp_path/'owned-server.log'
    path.write_text(json.dumps({'status':'FAIL','code':'CORE_SERVER_FAILED','errno':10048,'winerror':10048,'private':'do not copy me'}))
    assert m.child_failure(path)=={'cause_code':'CORE_SERVER_FAILED','errno':10048,'winerror':10048}
    path.write_text('private traceback not structured')
    assert m.child_failure(path)=={}


def test_safe_api_errors_are_persisted_in_bounded_owned_reports(tmp_path):
    import json
    m=control()
    assert hasattr(m,'APIErrorSink'),'safe persisted API diagnostics missing'
    sink=m.APIErrorSink(tmp_path)
    try:
        sink('INTERNAL_ERROR')
        assert json.loads((tmp_path/'logs/latest-core-api-error.json').read_text())['code']=='INTERNAL_ERROR'
        assert 'INTERNAL_ERROR' in (tmp_path/'logs/core-api-errors.log').read_text()
        with pytest.raises(m.ControlFailure): sink('token=private')
        assert 'private' not in (tmp_path/'logs/core-api-errors.log').read_text()
    finally: sink.close()
