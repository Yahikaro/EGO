"""2026-11-06: tunnel for access from outside the home network — the gate, the manager, the API."""
import asyncio
import json
import secrets
import sys
import time
import types

import httpx
import pytest

from server.ego_core import tunnel as T
from server.ego_core.api import create_app
from test_m17_accounts import Client

WORDS = 'amber.river.falcon.mist.orbit'
KEY = 'k' * 40


async def echo(scope, receive, send):
    """Inner app: tells what it saw (client address and path)."""
    body = json.dumps({'client': list(scope['client']), 'path': scope['path'], 'tunnel': scope.get('ego_tunnel', False)}).encode()
    await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]})
    await send({'type': 'http.response.body', 'body': body})


def call(app, method, path, headers=None):
    async def run():
        transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 50000))   # cloudflared is on this PC
        async with httpx.AsyncClient(transport=transport, base_url='https://x.trycloudflare.com') as c:
            return await c.request(method, path, headers=headers or {}, follow_redirects=False)
    return asyncio.run(run())


def test_words_are_random_and_dotted():
    a, b = T.make_words(), T.make_words()
    assert a != b and a.count('.') == 4 and all(w in T.WORDS for w in a.split('.'))
    assert len(set(T.WORDS)) == len(T.WORDS) >= 200


def test_gate_hides_everything_without_the_words():
    gate = T.TunnelGate(echo, WORDS, KEY)
    for path in ('/', '/app/', '/v1/account/state', '/v1/tunnel', '/ego/', '/ego/amber.river.falcon.mist.wrong/'):
        r = call(gate, 'GET', path)
        assert r.status_code == 404 and r.text == 'Not Found', path
    r = call(gate, 'GET', '/v1/account/state', {'cookie': 'ego_tunnel=' + 'x' * 40})
    assert r.status_code == 404


def test_words_open_the_door_and_everything_stays_remote():
    gate = T.TunnelGate(echo, WORDS, KEY)
    r = call(gate, 'GET', f'/ego/{WORDS}/')
    assert r.status_code == 303 and r.headers['location'] == '/app/'
    cookie = r.headers['set-cookie']
    assert cookie.startswith(f'ego_tunnel={KEY}') and 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=Lax' in cookie
    seen = call(gate, 'GET', '/v1/account/state', {'cookie': f'other=1; ego_tunnel={KEY}'}).json()
    assert seen['client'][0] == '0.0.0.0' and seen['tunnel'] is True and seen['path'] == '/v1/account/state'
    # Cloudflare's visitor address is kept for the journal, but loopback can never be claimed
    assert call(gate, 'GET', '/x', {'cookie': f'ego_tunnel={KEY}', 'cf-connecting-ip': '203.0.113.7'}).json()['client'][0] == '203.0.113.7'
    for spoof in ('127.0.0.1', '::1', '127.8.8.8', 'localhost'):
        assert call(gate, 'GET', '/x', {'cookie': f'ego_tunnel={KEY}', 'cf-connecting-ip': spoof}).json()['client'][0] == '0.0.0.0'


def test_tunnel_requests_cannot_use_pc_powers(tmp_path):
    client_dir = tmp_path / 'client'
    client_dir.mkdir()
    (client_dir / 'index.html').write_text('<!doctype html><title>EGO</title>', encoding='utf-8')
    service = types.SimpleNamespace(store=types.SimpleNamespace(list_chats=lambda: []), last_api_error=None, installation_id=None)
    admin = secrets.token_urlsafe(32)
    app = create_app(service, admin, client_dir=client_dir)
    gate = T.TunnelGate(app, WORDS, KEY)
    cookie = {'cookie': f'ego_tunnel={KEY}'}
    for method, path in (('GET', '/v1/chats'), ('POST', '/v1/client/launch'), ('GET', '/v1/tunnel'), ('POST', '/v1/tunnel/stop')):
        r = call(gate, method, path, {**cookie, 'authorization': 'Bearer ' + admin})   # even with the PC's admin token
        assert r.status_code == 401, (path, r.status_code)
    r = call(gate, 'POST', '/v1/client/session', {**cookie, 'content-type': 'application/json'})
    assert r.status_code in (401, 422)
    assert call(gate, 'GET', '/app/', cookie).status_code == 200


def test_tunnel_api_is_for_control_center_only(tmp_path):
    service = types.SimpleNamespace(store=types.SimpleNamespace(list_chats=lambda: []), last_api_error=None, installation_id=None)
    admin = secrets.token_urlsafe(32)
    fake = types.SimpleNamespace(status=lambda: {'state': 'off'}, start=lambda: {'state': 'starting'}, stop=lambda: {'state': 'off'})
    app = create_app(service, admin, tunnel=fake)
    pc = Client(app)
    assert pc.request('GET', '/v1/tunnel').status_code == 401                       # no token
    pc.headers = {'Authorization': 'Bearer ' + admin}
    assert pc.request('GET', '/v1/tunnel').json() == {'state': 'off'}
    assert pc.request('POST', '/v1/tunnel/start').json() == {'state': 'starting'}
    laptop = Client(app, host='192.168.1.20')
    laptop.headers = {'Authorization': 'Bearer ' + admin}
    assert laptop.request('POST', '/v1/tunnel/start').status_code == 401
    assert 'tunnel' not in json.dumps(app.openapi())


FAKE = r'''
import sys, time
sys.stderr.write("INF Requesting new quick Tunnel on trycloudflare.com...\n")
sys.stderr.write("INF |  https://brave-otter-lamp-cello.trycloudflare.com  |\n"); sys.stderr.flush()
time.sleep(60)
'''


def test_manager_starts_reports_link_and_stops(tmp_path):
    fake = tmp_path / 'fake_cloudflared.py'
    fake.write_text(FAKE, encoding='utf-8')
    seen = {}
    def command(origin):
        seen['origin'] = origin
        return [sys.executable, str(fake), origin]
    t = T.Tunnel(tmp_path, echo, command=command, start_timeout=10)
    assert t.status()['state'] == 'off' and t.status()['url'] is None
    assert t.start()['state'] == 'starting'
    deadline = time.monotonic() + 10
    while t.status()['state'] == 'starting' and time.monotonic() < deadline:
        time.sleep(.05)
    status = t.status()
    assert status['state'] == 'on', status
    assert status['url'].startswith('https://brave-otter-lamp-cello.trycloudflare.com/ego/') and status['url'].endswith('/')
    words = status['url'].rsplit('/ego/', 1)[1].strip('/')
    assert words.count('.') == 4
    origin = seen['origin']
    assert origin.startswith('http://127.0.0.1:')
    r = httpx.get(origin + '/v1/x')
    assert r.status_code == 404                                                     # no words, nothing
    r = httpx.get(f'{origin}/ego/{words}/', follow_redirects=False)
    assert r.status_code == 303
    key = r.cookies.get('ego_tunnel') or r.headers['set-cookie'].split(';')[0].split('=', 1)[1]
    assert httpx.get(origin + '/v1/x', headers={'cookie': f'ego_tunnel={key}'}).json()['client'][0] == '0.0.0.0'
    process = t._process
    assert t.stop()['state'] == 'off' and t.status()['url'] is None
    assert process.poll() is not None
    with pytest.raises(httpx.HTTPError):
        httpx.get(origin + '/v1/x', timeout=2)
    # a new start gives new words
    t2 = T.Tunnel(tmp_path, echo, command=command, start_timeout=10)
    t2.start()
    deadline = time.monotonic() + 10
    while t2.status()['state'] == 'starting' and time.monotonic() < deadline:
        time.sleep(.05)
    assert t2.status()['url'].rsplit('/ego/', 1)[1].strip('/') != words
    t2.stop()


def test_manager_reports_missing_binary_and_failed_start(tmp_path):
    t = T.Tunnel(tmp_path, echo)
    assert t.status()['available'] is False
    assert t.start() == {**t.status(), 'state': 'error', 'error': 'TUNNEL_BINARY_MISSING'}
    silent = tmp_path / 'silent.py'
    silent.write_text('import time; time.sleep(30)', encoding='utf-8')
    t = T.Tunnel(tmp_path, echo, command=lambda origin: [sys.executable, str(silent)], start_timeout=1)
    t.start()
    time.sleep(1.8)
    assert t.status()['state'] == 'error' and t.status()['error'] == 'TUNNEL_START_FAILED'
    quits = tmp_path / 'quits.py'
    quits.write_text('import sys; sys.stderr.write("https://a-b.trycloudflare.com\\n")', encoding='utf-8')
    t = T.Tunnel(tmp_path, echo, command=lambda origin: [sys.executable, str(quits)], start_timeout=5)
    t.start()
    time.sleep(1.5)
    assert t.status()['state'] == 'error' and t.status()['error'] == 'TUNNEL_STOPPED' and t.status()['url'] is None
