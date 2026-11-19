"""Stage 4i: optional HTTPS listener for the home network; remote devices need an account."""
import asyncio
import json
import secrets
import socket
import ssl
import types

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI

from server.ego_core import lan
from server.ego_core.accounts import Accounts
from server.ego_core.api import create_app
from server.ego_core.store import MainStore
from test_m17_accounts import EMAIL, PASSWORD, Clock

PASSWORD_TLS = secrets.token_urlsafe(32)


def test_settings_default_off_and_validate(tmp_path):
    assert lan.load_settings(tmp_path) == {'lan_access': False, 'lan_port': 18444}
    (tmp_path / 'settings').mkdir()
    lan.settings_path(tmp_path).write_text('{broken', encoding='utf-8')
    assert lan.load_settings(tmp_path)['lan_access'] is False
    assert lan.save_settings(tmp_path, lan_access=True, lan_port=19000) == {'lan_access': True, 'lan_port': 19000}
    with pytest.raises(ValueError):
        lan.save_settings(tmp_path, lan_access='yes')
    lan.settings_path(tmp_path).write_text(json.dumps({'lan_access': True, 'lan_port': 80}), encoding='utf-8')
    assert lan.load_settings(tmp_path) == {'lan_access': True, 'lan_port': 18444}   # privileged port ignored


def test_certificate_covers_addresses_is_reused_and_key_is_encrypted(tmp_path):
    cert, key, fp = lan.ensure_certificate(tmp_path, PASSWORD_TLS, ['192.168.1.100'])
    certificate = x509.load_pem_x509_certificate(cert.read_bytes())
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {str(i) for i in san.get_values_for_type(x509.IPAddress)} == {'127.0.0.1', '192.168.1.100'}
    assert 'localhost' in san.get_values_for_type(x509.DNSName)
    assert len(fp.split(':')) == 32
    with pytest.raises(TypeError):
        serialization.load_pem_private_key(key.read_bytes(), None)          # never stored in clear
    assert lan.ensure_certificate(tmp_path, PASSWORD_TLS, ['192.168.1.100'])[2] == fp      # reused
    assert lan.ensure_certificate(tmp_path, PASSWORD_TLS, ['10.0.0.7'])[2] != fp          # new network → new cert
    assert lan.ensure_certificate(tmp_path, 'another-password-entirely-xx', ['10.0.0.7'])[2] != fp


def test_private_addresses_are_private_ipv4_only():
    for value in lan.private_addresses():
        assert value.count('.') == 3 and not value.startswith('127.')


class Remote:
    def __init__(self, app, host):
        self.app, self.host, self.headers = app, host, {}
    def request(self, method, path, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=self.app, client=(self.host, 50000))
            async with httpx.AsyncClient(transport=transport, base_url='https://pc', headers=self.headers) as c:
                return await c.request(method, path, **kwargs)
        return asyncio.run(run())


def test_remote_devices_only_with_an_account_session(tmp_path):
    store = MainStore(tmp_path / 'main.db', secrets.token_bytes(32))
    try:
        accounts = Accounts(store, clock=Clock())
        service = types.SimpleNamespace(store=store, last_api_error=None, installation_id=None)
        admin = secrets.token_urlsafe(32)
        status = {'enabled': True, 'port': 18444, 'addresses': ['192.168.1.100'], 'fingerprint': 'AA:BB', 'error': None}
        app = create_app(service, admin, accounts=accounts, network=lambda: status)
        pc, laptop = Remote(app, '127.0.0.1'), Remote(app, '192.168.1.20')
        laptop.headers = {'Authorization': 'Bearer ' + admin}
        assert laptop.request('GET', '/v1/chats').status_code == 401              # admin token is PC-only
        code = Remote(app, '127.0.0.1')
        code.headers = {'Authorization': 'Bearer ' + admin}
        launch = code.request('POST', '/v1/client/launch').json()['code']
        assert laptop.request('POST', '/v1/client/session', json={'code': launch}).status_code == 401
        assert pc.request('POST', '/v1/client/session', json={'code': launch}).status_code == 200
        assert laptop.request('POST', '/v1/accounts/users', json={'email': EMAIL, 'password': PASSWORD}).status_code == 401
        accounts.add_user(EMAIL, PASSWORD)
        accounts.clock.t += 60
        laptop.headers = {}
        assert laptop.request('GET', '/health').status_code == 200
        token = laptop.request('POST', '/v1/account/login', json={'email': EMAIL, 'password': PASSWORD}).json()['token']
        laptop.headers = {'Authorization': 'Bearer ' + token}
        assert laptop.request('GET', '/v1/chats').status_code == 200
        assert laptop.request('POST', '/v1/client/launch').status_code == 401
        network = laptop.request('GET', '/v1/network').json()
        assert network['urls'] == ['https://192.168.1.100:18444/app/'] and network['fingerprint'] == 'AA:BB'
    finally:
        store.close()


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_lan_listener_serves_https_only_and_reports_status(tmp_path, monkeypatch):
    from server.ego_core import control
    port = _free_port()
    lan.save_settings(tmp_path, lan_access=True, lan_port=port)
    (tmp_path / 'data').mkdir()
    keys = tmp_path / 'keys'; keys.mkdir()
    monkeypatch.setattr(lan, 'private_addresses', lambda: ['127.0.0.1'])
    core = types.SimpleNamespace(identity=types.SimpleNamespace(certfile=keys / 'identity.crt.pem',
                                                                secrets=types.SimpleNamespace(tls_password=PASSWORD_TLS)))
    app = FastAPI()
    app.get('/health')(lambda: {'status': 'ok'})
    network = {}
    server, thread, sockets = control._start_lan(tmp_path, core, app, network)
    try:
        assert server is not None and network['addresses'] == ['127.0.0.1'] and network['error'] is None
        context = ssl.create_default_context(cafile=str(keys / lan.CERT))
        for _ in range(100):
            try:
                response = httpx.get(f'https://127.0.0.1:{port}/health', verify=context, timeout=2)
                break
            except httpx.ConnectError:
                import time; time.sleep(.05)
        assert response.json() == {'status': 'ok'}
        with pytest.raises(httpx.HTTPError):
            httpx.get(f'http://127.0.0.1:{port}/health', timeout=2)                  # no plain HTTP
        status = lan.read_status(tmp_path)
        assert status['urls'] == [f'https://127.0.0.1:{port}/app/'] and status['fingerprint'] == network['fingerprint']
    finally:
        server.should_exit = True
        thread.join(5)
        for sock in sockets:
            sock.close()


def test_lan_disabled_or_without_address_starts_nothing(tmp_path, monkeypatch):
    from server.ego_core import control
    (tmp_path / 'data').mkdir()
    core = types.SimpleNamespace(identity=types.SimpleNamespace(certfile=tmp_path / 'x', secrets=types.SimpleNamespace(tls_password=PASSWORD_TLS)))
    network = {}
    assert control._start_lan(tmp_path, core, FastAPI(), network)[0] is None
    assert lan.read_status(tmp_path)['enabled'] is False
    lan.save_settings(tmp_path, lan_access=True)
    monkeypatch.setattr(lan, 'private_addresses', lambda: [])
    assert control._start_lan(tmp_path, core, FastAPI(), network)[0] is None
    assert lan.read_status(tmp_path)['error'] == 'LAN_NO_ADDRESS'
