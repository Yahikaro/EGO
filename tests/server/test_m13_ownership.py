"""Installation ownership and lease-based Core lifecycle regressions."""
from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import secrets
import sys
import threading
import time

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from tests.server.test_core_api import LocalClient

from server.ego_core import control
from server.ego_core.config import CoreConfig, save_config
from server.ego_core.keys import KeyVault


@pytest.fixture
def vault_factory():
    cipher = AESGCM(secrets.token_bytes(32))
    def protect(data):
        nonce = secrets.token_bytes(12)
        return nonce + cipher.encrypt(nonce, data, None)
    def unprotect(data):
        return cipher.decrypt(data[:12], data[12:], None)
    return lambda directory: KeyVault(directory, protect=protect, unprotect=unprotect)


def test_assembled_core_publishes_canonical_root_identity(tmp_path, vault_factory):
    control.initialize(tmp_path, vault_factory=vault_factory)
    core = control.assemble(tmp_path, vault_factory=vault_factory)
    try:
        expected = hashlib.sha256(os.path.normcase(str(tmp_path.resolve())).encode('utf-8')).hexdigest()
        assert core.service.status().get('installation_id') == expected
        assert control.installation_id(tmp_path / 'data' / '..') == expected
    finally:
        core.close()


def test_assembled_core_preserves_data_auth_and_guards_lifecycle_owner(tmp_path, vault_factory):
    control.initialize(tmp_path, vault_factory=vault_factory)
    core = control.assemble(tmp_path, vault_factory=vault_factory)
    try:
        token = core.identity.secrets.admin_token
        with nullcontext(LocalClient(core.app)) as client:
            # Existing bearer-only status/data clients remain compatible.
            response=client.get('/v1/system/status',headers={'Authorization':'Bearer '+token})
            assert response.status_code==200
            for path in ('/v1/system/shutdown','/v1/runtime/restart','/v1/runtime/configure'):
                response=client.post(path,headers={'Authorization':'Bearer '+token},json={})
                assert response.status_code==403
            for owner in ('different-installation',):
                headers = {'Authorization': 'Bearer ' + token}
                if owner is not None:
                    headers['X-EGO-Installation'] = owner
                response = client.get('/v1/system/status', headers=headers)
                assert response.status_code == 403
                assert response.json()['code'] == 'CORE_INSTALLATION_MISMATCH'
            response = client.get('/v1/system/status', headers={
                'Authorization': 'Bearer ' + token,
                'X-EGO-Installation': control.installation_id(tmp_path)})
            assert response.status_code == 200
    finally:
        core.close()


@pytest.mark.parametrize('returned_owner', [None, 'foreign'])
def test_client_rejects_authenticated_legacy_or_foreign_status(tmp_path, vault_factory, monkeypatch, returned_owner):
    control.initialize(tmp_path, vault_factory=vault_factory)
    response = {'storage': 'ready'}
    if returned_owner is not None:
        response['installation_id'] = returned_owner
    original_client = httpx.Client
    def handler(request):
        return httpx.Response(200, json=response)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(
        transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(control.ControlFailure, match='CORE_INSTALLATION_MISMATCH'):
        control.request(tmp_path, 'GET', '/v1/system/status', vault_factory=vault_factory)


def test_control_client_sends_owner_header_and_preserves_http_setting(tmp_path, vault_factory, monkeypatch):
    control.initialize(tmp_path, vault_factory=vault_factory)
    original_client = httpx.Client
    seen = []
    owner = hashlib.sha256(os.path.normcase(str(tmp_path.resolve())).encode('utf-8')).hexdigest()
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'installation_id': owner})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(
        transport=httpx.MockTransport(handler), **kwargs))
    control.request(tmp_path, 'GET', '/v1/system/status', vault_factory=vault_factory)
    control.request(tmp_path, 'POST', '/v1/system/shutdown', payload={}, vault_factory=vault_factory)
    assert [r.headers.get('X-EGO-Installation') for r in seen] == [owner, owner, owner]
    assert all(r.url.scheme == 'http' for r in seen)
    assert all(r.headers['Authorization'].startswith('Bearer ') for r in seen)


def test_stop_waits_for_actual_lease_after_authenticated_api_disappears(tmp_path, monkeypatch):
    def offline(*args, **kwargs):
        raise control.ControlFailure('CORE_OFFLINE')
    monkeypatch.setattr(control, 'request', offline)
    acquired, release = threading.Event(), threading.Event()
    def holder():
        with control.CoreLease(tmp_path / 'data'):
            acquired.set()
            release.wait(2)
    (tmp_path / 'data').mkdir()
    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(1)
    timer = threading.Timer(.2, release.set)
    timer.start()
    started = time.monotonic()
    try:
        result = control.stop(tmp_path)
        assert time.monotonic() - started >= .15
        assert result['status'] == 'PASS'
    finally:
        release.set()
        thread.join(2)
        timer.join(1)


def test_stop_with_busy_lease_has_bounded_timeout(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    def offline(*args, **kwargs):
        raise control.ControlFailure('CORE_OFFLINE')
    monkeypatch.setattr(control, 'request', offline)
    monkeypatch.setattr(control, 'STOP_TIMEOUT', .15, raising=False)
    started = time.monotonic()
    with control.CoreLease(tmp_path / 'data'):
        with pytest.raises(control.ControlFailure, match='CORE_STOP_TIMEOUT'):
            control.stop(tmp_path)
    assert time.monotonic() - started < 1


def test_stop_offline_preserves_inaccessible_data_failure(tmp_path, monkeypatch):
    def offline(*args, **kwargs):
        raise control.ControlFailure('CORE_OFFLINE')
    monkeypatch.setattr(control, 'request', offline)
    # A file at data is equally inaccessible to the lease on Linux and Windows.
    (tmp_path / 'data').write_bytes(b'not a directory')
    with pytest.raises(OSError):
        control.stop(tmp_path)


def test_assemble_preloads_valid_model_without_generation(tmp_path, vault_factory):
    from runtime.adapters.llama_cpp import LlamaRuntime
    from runtime.adapters.ports import choose_loopback_port
    control.initialize(tmp_path, vault_factory=vault_factory)
    model = tmp_path / 'fixture.gguf'
    model.write_bytes(b'GGUFtest')
    save_config(tmp_path / 'data/config.json', CoreConfig(
        binary=sys.executable, model=str(model), runtime_port=choose_loopback_port(), startup_timeout=5.))
    fixture = Path(__file__).parents[2] / 'tests/fixtures/fake_llama.py'
    def factory(config):
        runtime = LlamaRuntime(config)
        runtime._binary_command = lambda: [getattr(sys,'_base_executable',sys.executable),str(fixture)]
        return runtime
    core = control.assemble(tmp_path, vault_factory=vault_factory, runtime_factory=factory)
    runtime = core.service.runtime
    try:
        deadline = time.monotonic() + 5
        while not core.service.status().get('model_ready'):
            assert time.monotonic() < deadline, core.service.status()
            time.sleep(.03)
        assert core.service.status()['model_ready'] is True
        assert runtime.process.poll() is None
    finally:
        core.close()
    assert runtime.process is None


def test_assemble_invalid_model_keeps_editable_core(tmp_path, vault_factory):
    control.initialize(tmp_path, vault_factory=vault_factory)
    save_config(tmp_path / 'data/config.json', CoreConfig(
        binary=sys.executable, model=str(tmp_path / 'missing.gguf')))
    core = control.assemble(tmp_path, vault_factory=vault_factory)
    try:
        deadline = time.monotonic() + 2
        while core.service.status().get('runtime_state') != 'ERROR':
            assert time.monotonic() < deadline
            time.sleep(.01)
        with nullcontext(LocalClient(core.app)) as client:
            response = client.get('/v1/chats', headers={
                'Authorization': 'Bearer ' + core.identity.secrets.admin_token,
                'X-EGO-Installation': control.installation_id(tmp_path)})
            assert response.status_code == 200
    finally:
        core.close()


def test_startup_control_eof_detaches_but_explicit_stop_cancels():
    import io
    detached = control.startup_control_event(io.StringIO(''))
    assert not detached.wait(.1)
    cancelled = control.startup_control_event(io.StringIO('STOP\n'))
    assert cancelled.wait(1)


def _subprocess_vault(directory):
    """Test-only protector reproducible by the disposable Core subprocess."""
    cipher = AESGCM(b'\x17' * 32)
    def protect(data):
        nonce = secrets.token_bytes(12)
        return nonce + cipher.encrypt(nonce, data, None)
    return KeyVault(directory, protect=protect,
                    unprotect=lambda data: cipher.decrypt(data[:12], data[12:], None))


def _live_process(pid):
    import psutil
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.mark.parametrize('outcome', ['cancel', 'timeout', 'ready'])
def test_start_controls_loading_core_and_its_real_runtime_child(tmp_path, monkeypatch, outcome):
    import subprocess
    from runtime.adapters.ports import choose_loopback_port
    control.initialize(tmp_path, vault_factory=_subprocess_vault)
    api_port = choose_loopback_port()
    runtime_port = choose_loopback_port()
    while runtime_port == api_port:
        runtime_port = choose_loopback_port()
    model = tmp_path / 'fixture.gguf'
    model.write_bytes(b'GGUFtest')
    save_config(tmp_path / 'data/config.json', CoreConfig(binary=sys.executable, model=str(model),
        api_port=api_port, runtime_port=runtime_port, startup_timeout=30.))
    pidfile = tmp_path / 'runtime.pid'
    # The real adapter owns a real process that never finishes model loading.
    bootstrap = '''
import sys
from pathlib import Path
from server.ego_core import control
from tests.server.test_m13_ownership import _subprocess_vault
from runtime.adapters.llama_cpp import LlamaRuntime
root = Path(sys.argv[1])
def factory(config):
    runtime = LlamaRuntime(config)
    child = "import os,time;from pathlib import Path;Path(%r).write_text(str(os.getpid()));time.sleep(60)" % str(root/'runtime.pid')
    runtime._binary_command = lambda: [getattr(sys,'_base_executable',sys.executable),'-c', child]
    return runtime
control.serve(root, vault_factory=_subprocess_vault, runtime_factory=factory, startup_control=sys.stdin)
'''
    real_popen = subprocess.Popen
    children = []
    def popen(command, **kwargs):
        assert '--startup-control' in command
        child = real_popen([sys.executable, '-c', bootstrap, str(tmp_path)], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(control.subprocess, 'Popen', popen)
    monkeypatch.setattr(control, 'KeyVault', _subprocess_vault)
    def offline(*args, **kwargs):
        raise control.ControlFailure('CORE_OFFLINE')
    real_request = control.request
    def owned_request(*args, **kwargs):
        kwargs.setdefault('vault_factory', _subprocess_vault)
        return real_request(*args, **kwargs)
    monkeypatch.setattr(control, 'request', owned_request if outcome == 'ready' else offline)
    if outcome == 'timeout':
        monkeypatch.setattr(control, 'START_TIMEOUT', 2.5)
    cancel = threading.Event()
    def cancel_loading():
        deadline = time.monotonic() + 8
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        if outcome == 'cancel':
            cancel.set()
    watcher = threading.Thread(target=cancel_loading)
    watcher.start()
    try:
        if outcome == 'timeout':
            with pytest.raises(control.ControlFailure, match='CORE_START_TIMEOUT'):
                control.start(tmp_path, cancel=cancel)
        else:
            result = control.start(tmp_path, cancel=cancel)
            assert result['code'] == ('CORE_STARTED' if outcome == 'ready' else 'CORE_START_CANCELLED')
        if outcome == 'ready':
            assert result['core']['runtime_state'] == 'LOADING'
            assert result['core']['model_ready'] is False
            assert children[0].stdin.closed
            assert children[0].poll() is None
            deadline = time.monotonic() + 3
            while not pidfile.exists():
                assert time.monotonic() < deadline
                time.sleep(.01)
            assert control.stop(tmp_path)['code'] == 'CORE_STOPPED'
            children[0].wait(3)
        assert pidfile.exists(), list((tmp_path / 'logs').glob('*'))
        runtime_pid = int(pidfile.read_text())
        assert children[0].poll() is not None
        assert not _live_process(runtime_pid)
        with control.CoreLease(tmp_path / 'data'):
            pass
    finally:
        cancel.set()
        watcher.join(9)
        for child in children:
            if child.poll() is None:
                control._cleanup_started_child(child)


def test_forced_start_cleanup_kills_only_new_owned_session(tmp_path, monkeypatch):
    import subprocess
    import signal
    if os.name == 'nt':
        pytest.skip('POSIX session fallback; native Windows job tested separately')
    child_pid = tmp_path / 'child.pid'
    owned_bootstrap = '''
import subprocess,sys,time
from pathlib import Path
child = subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c', 'import time;time.sleep(60)'])
Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(60)
'''
    foreign = subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c', 'import time;time.sleep(60)'], start_new_session=True)
    owned = subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c', owned_bootstrap, str(child_pid)],
                             stdin=subprocess.PIPE, start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        while not child_pid.exists():
            assert time.monotonic() < deadline
            time.sleep(.01)
        pid = int(child_pid.read_text())
        monkeypatch.setattr(control, 'CHILD_CLEANUP_TIMEOUT', .15)
        control._cleanup_started_child(owned)
        assert owned.poll() is not None
        deadline = time.monotonic() + 2
        while _live_process(pid) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not _live_process(pid)
        assert foreign.poll() is None
    finally:
        if owned.poll() is None:
            os.killpg(owned.pid, signal.SIGKILL)
            owned.wait(3)
        foreign.kill()
        foreign.wait(3)


def test_stop_refuses_legacy_listener_before_sending_shutdown(tmp_path, vault_factory, monkeypatch):
    control.initialize(tmp_path, vault_factory=vault_factory)
    original_client = httpx.Client
    requests = []
    def handler(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={'storage': 'ready'})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(
        transport=httpx.MockTransport(handler), **kwargs))
    original_request = control.request
    def request(*args, **kwargs):
        kwargs.setdefault('vault_factory', vault_factory)
        return original_request(*args, **kwargs)
    monkeypatch.setattr(control, 'request', request)
    # This installation's data lease is free, so no Core of ours is running:
    # stop() reports that and sends nothing at all to the unverified listener.
    assert control.stop(tmp_path)['code'] == 'CORE_ALREADY_STOPPED'
    assert requests == []
    # While our lease is held, the listener is still verified before shutdown.
    monkeypatch.setattr(control, '_lease_available', lambda root: False)
    with pytest.raises(control.ControlFailure, match='CORE_INSTALLATION_MISMATCH'):
        control.stop(tmp_path)
    assert requests == [('GET', '/v1/system/status')]


def test_start_cleanup_accepts_child_that_has_already_exited():
    import subprocess
    child = subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c', 'pass'], stdin=subprocess.PIPE,
                             start_new_session=os.name != 'nt')
    child.wait(3)
    control._cleanup_started_child(child)
    assert child.stdin.closed

def test_authenticated_runtime_busy_error_is_retained_without_raw_body(tmp_path,vault_factory,monkeypatch):
    control.initialize(tmp_path,vault_factory=vault_factory)
    original=httpx.Client
    def handler(request):
        if request.url.path=='/v1/system/status':
            return httpx.Response(200,json={'installation_id':control.installation_id(tmp_path)})
        return httpx.Response(409,json={'code':'CORE_BUSY','private':'DO_NOT_LOG_PRIVATE'})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(control.ControlFailure) as error:
        control.request(tmp_path,'POST','/v1/runtime/restart',payload={},vault_factory=vault_factory)
    assert error.value.code=='CORE_BUSY'
    assert 'DO_NOT_LOG_PRIVATE' not in str(error.value)


def test_runtime_error_audit_preserves_numeric_codes_but_never_private_fields(tmp_path):
    audit=control.APIErrorSink(tmp_path)
    try:
        audit('PORT_IN_USE',diagnostics={'errno':98,'winerror':10048,'state':'ERROR','prompt':'DO_NOT_LOG_PRIVATE'})
        import json
        result=json.loads((tmp_path/'logs/latest-core-api-error.json').read_text())
        assert result['errno']==98 and result['winerror']==10048
        assert result['runtime_state']=='ERROR'
        assert 'DO_NOT_LOG_PRIVATE' not in str(result)
    finally:audit.close()
