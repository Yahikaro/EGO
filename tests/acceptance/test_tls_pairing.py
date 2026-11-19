import concurrent.futures
import socket
import ssl
import threading
import time

import pytest
import uvicorn

from server.ego_core.vault.tls_pairing.certificates import TestAuthority
from server.ego_core.vault.tls_pairing.client import PinnedClient, PinMismatch
from server.ego_core.vault.tls_pairing.pairing import PairingStore, PairingFailure
from server.ego_core.vault.tls_pairing.app import create_app


def test_pairing_requires_local_approval_and_claim_is_once():
    store = PairingStore()
    ticket, code = store.issue()
    pending, secret = store.redeem(ticket, code)
    with pytest.raises(PairingFailure, match='APPROVAL_REQUIRED'):
        store.claim(pending, secret)
    store.approve(pending)
    device, token = store.claim(pending, secret)
    assert store.authenticate(token) == device
    with pytest.raises(PairingFailure):
        store.claim(pending, secret)
    with pytest.raises(PairingFailure):
        store.redeem(ticket, code)
    store.revoke(device)
    with pytest.raises(PairingFailure, match='UNAUTHORIZED'):
        store.authenticate(token)


def test_pairing_expiry_and_lockout():
    now = [10.0]
    store = PairingStore(clock=lambda: now[0], ttl=60, max_attempts=3)
    ticket, code = store.issue()
    for _ in range(3):
        with pytest.raises(PairingFailure):
            store.redeem(ticket, 'wrong')
    with pytest.raises(PairingFailure, match='LOCKED'):
        store.redeem(ticket, code)
    ticket, code = store.issue()
    now[0] += 60
    with pytest.raises(PairingFailure, match='EXPIRED'):
        store.redeem(ticket, code)


def test_concurrent_pairing_only_one_winner():
    store = PairingStore()
    ticket, code = store.issue()

    def attempt(_):
        try:
            store.redeem(ticket, code)
            return True
        except PairingFailure:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(8))) == 1


@pytest.fixture
def live_server(tmp_path):
    ca = TestAuthority()
    cert, key, pin = ca.issue(tmp_path)
    store = PairingStore()
    app = create_app(store)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    listener.listen()
    config = uvicorn.Config(app, host='127.0.0.1', port=port, ssl_certfile=str(cert),
                            ssl_keyfile=str(key), log_config=None, access_log=False,
                            log_level='critical', ws='none')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]})
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    yield store, ca, pin, port, app
    server.should_exit = True
    thread.join(5)
    listener.close()
    assert not thread.is_alive()


def test_real_https_pin_pairing_revoke(live_server):
    store, ca, pin, port, _ = live_server
    client = PinnedClient(ca.pem(), pin, port=port)
    assert client.request('GET', '/health')[0] == 200
    ticket, code = store.issue()
    status, pending = client.request('POST', '/pairing/redeem', {'ticket': ticket, 'code': code})
    assert status == 200
    assert client.request('POST', '/pairing/claim', pending)[0] == 403
    store.approve(pending['pending'])
    status, auth = client.request('POST', '/pairing/claim', pending)
    assert status == 200
    assert client.request('GET', '/protected', token=auth['token'])[0] == 200
    store.revoke(auth['device'])
    assert client.request('GET', '/protected', token=auth['token'])[0] == 401


def test_wrong_ca_rejected_before_http(live_server):
    _, ca, pin, port, app = live_server
    client = PinnedClient(TestAuthority().pem(), pin, port=port)
    before = app.state.request_count
    with pytest.raises(ssl.SSLCertVerificationError):
        client.request('GET', '/health')
    assert app.state.request_count == before


def test_replaced_public_key_rejected_before_credentials(live_server, tmp_path):
    _, ca, pin, port, app = live_server
    _, _, other_pin = ca.issue(tmp_path / 'other')
    before = app.state.request_count
    with pytest.raises(PinMismatch):
        PinnedClient(ca.pem(), other_pin, port=port).request(
            'GET', '/protected', token='synthetic-do-not-transmit')
    assert app.state.request_count == before


def test_wrong_hostname_rejected(live_server):
    _, ca, pin, port, app = live_server
    # Same loopback address but verify a name absent from the SAN certificate.
    context = ssl.create_default_context(cadata=ca.pem())
    with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
        with pytest.raises(ssl.SSLCertVerificationError):
            context.wrap_socket(connection, server_hostname='wrong.invalid')


def test_remote_cannot_issue_approve_or_revoke(live_server):
    _, ca, pin, port, _ = live_server
    client = PinnedClient(ca.pem(), pin, port=port)
    for path in ['/pairing/issue', '/pairing/approve', '/devices/revoke']:
        assert client.request('POST', path, {})[0] == 404
