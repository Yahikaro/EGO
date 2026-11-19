"""Access from outside the home network (2026-11-06).

Off until the owner presses «Включить» in Control Center; never starts on its own.
Core then opens one more listener on 127.0.0.1 (random port) and starts Cloudflare's
official client (runtime\\tunnel\\cloudflared.exe, a free «quick tunnel», no account).
Cloudflare gives a random https://…trycloudflare.com address; EGO adds its own secret of
random words joined by dots, so the link looks like
    https://<cloudflare words>.trycloudflare.com/ego/amber.river.falcon.mist.orbit/

Rules of the tunnel listener (TunnelGate):
  * without the words nothing answers — every path is a plain 404;
  * the words open the door once per browser: an HttpOnly cookie, then /app/;
  * every request through the tunnel is treated as REMOTE, although cloudflared connects
    from this PC: the client address is replaced (never loopback), so only an EGO account
    session works — launch codes, admin and lifecycle stay PC-only;
  * the address and the words change at every start; stopping closes everything.
"""
from __future__ import annotations

import hmac
import ipaddress
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

WORDS = (
    'amber acorn alder anchor angel apple arbor arch arrow aspen atlas aurora autumn badge bamboo '
    'banner barley basil beacon bear berry birch bison blaze bloom bluff bolt bonfire border breeze '
    'brick bridge brook bubble cabin cactus camel candle canyon cape cargo castle cedar cello chalk '
    'cherry cinder citrus cliff clover cobalt comet compass copper coral cosmos cotton crane crater '
    'creek crown crystal cypress dahlia daisy dawn delta desert dew dolphin dove dragon drift dune '
    'eagle echo eclipse ember emerald falcon fern fiddle field finch fjord flame flint flora forest '
    'fox frost galaxy garden garnet geyser ginger glacier glade glow granite grove gull harbor hazel '
    'heron hill honey horizon inlet island ivory ivy jade jasmine jasper juniper kayak kelp kestrel '
    'kite lagoon lake lantern lark lava lemon lilac lily linen lotus lunar lynx magnet maple marble '
    'marsh meadow melody mesa meteor mint mirror mist moon moss nebula nectar nook oak oasis ocean '
    'olive onyx opal orbit orchid otter owl panda pearl pebble pepper petal pine planet plum polar '
    'pollen poppy prairie prism pulsar quartz quill rain raven reef ridge river robin rocket rose '
    'ruby saffron sable sail salmon sand sapphire savanna shell sierra silver sky slate snow sonnet '
    'sparrow spice spruce star stone storm summit sun swan tango thistle thunder tide tiger timber '
    'topaz torch tulip tundra twilight valley velvet violet vista volcano walnut wave willow wind '
    'winter wolf wren yarrow zenith zephyr'
).split()
WORD_COUNT = 5                                   # 5 of ~250 words ≈ 40 bits, plus account + code
COOKIE = 'ego_tunnel'
URL_RE = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com')
START_TIMEOUT = 45.0
_LOOPBACK_NETS = (ipaddress.ip_network('127.0.0.0/8'), ipaddress.ip_network('::1/128'))


def make_words(count=WORD_COUNT):
    return '.'.join(secrets.choice(WORDS) for _ in range(count))


def _remote_address(headers):
    """Cloudflare's view of the visitor (for the sign-in journal only); never loopback."""
    raw = headers.get(b'cf-connecting-ip', b'').decode('latin-1').strip()[:64]
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return '0.0.0.0'
    if any(address in net for net in _LOOPBACK_NETS):
        return '0.0.0.0'
    return str(address)


class TunnelGate:
    """ASGI wrapper for the tunnel listener only. The PC and home-network listeners
    never pass through it."""

    def __init__(self, app, words, cookie_key):
        self.app, self.words, self.cookie_key = app, words, cookie_key
        self.entry = '/ego/' + words
        self._failures, self._window = 0, time.monotonic()

    def _limited(self):
        now = time.monotonic()
        if now - self._window > 60:
            self._failures, self._window = 0, now
        self._failures += 1
        return self._failures > 120              # a flood of guesses: slow answers stop helping

    async def _plain(self, send, status, headers=()):
        body = b'Not Found' if status == 404 else b''
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', b'text/plain; charset=utf-8'), (b'cache-control', b'no-store'),
                                (b'referrer-policy', b'no-referrer'), (b'content-length', str(len(body)).encode()), *headers]})
        await send({'type': 'http.response.body', 'body': body})

    def _has_cookie(self, headers):
        for name, value in headers:
            if name != b'cookie':
                continue
            for part in value.decode('latin-1').split(';'):
                key, _, val = part.strip().partition('=')
                if key == COOKIE and hmac.compare_digest(val.encode(), self.cookie_key.encode()):
                    return True
        return False

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            return await self.app(scope, receive, send)
        if scope['type'] not in ('http', 'websocket'):
            return
        headers = scope.get('headers') or []
        path = scope.get('path', '')
        if scope['type'] == 'http' and path.rstrip('/') == self.entry.rstrip('/') and len(path) <= len(self.entry) + 1:
            cookie = (f'{COOKIE}={self.cookie_key}; Path=/; HttpOnly; Secure; SameSite=Lax').encode()
            return await self._plain(send, 303, [(b'location', b'/app/'), (b'set-cookie', cookie)])
        if not self._has_cookie(headers):
            if path.startswith('/ego/') and self._limited():
                return await self._plain(send, 429)
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await self._plain(send, 404)
        inner = dict(scope)
        inner['client'] = (_remote_address(dict(headers)), 0)     # remote, never loopback
        inner['ego_tunnel'] = True
        return await self.app(inner, receive, send)


class Tunnel:
    """Owner-controlled tunnel. start()/stop() are cheap and idempotent; status() has no secrets
    other than the link itself (shown only to the PC owner)."""

    def __init__(self, root, app=None, *, command=None, start_timeout=START_TIMEOUT):
        self.root, self.app = Path(root), app
        self.binary = self.root / 'runtime' / 'tunnel' / 'cloudflared.exe'
        self._command = command                  # tests inject a fake cloudflared
        self.start_timeout = start_timeout
        self._lock = threading.RLock()
        self._state, self._error, self._url, self._words = 'off', None, None, None
        self._process = self._server = self._thread = self._socket = None
        self._started = None

    # ---- status -----------------------------------------------------------------------------
    def status(self):
        with self._lock:
            link = f'{self._url}/ego/{self._words}/' if self._state == 'on' and self._url else None
            return {'state': self._state, 'url': link, 'error': self._error,
                    'available': self._command is not None or self.binary.is_file(),
                    'since': self._started}

    # ---- start / stop -----------------------------------------------------------------------
    def start(self):
        with self._lock:
            if self._state in ('starting', 'on'):
                return self.status()
            if self.app is None:
                self._fail('TUNNEL_UNAVAILABLE'); return self.status()
            if self._command is None and not self.binary.is_file():
                self._fail('TUNNEL_BINARY_MISSING'); return self.status()
            self._state, self._error, self._url = 'starting', None, None
            self._words = make_words()
            try:
                self._listen(TunnelGate(self.app, self._words, secrets.token_urlsafe(32)))
                self._spawn()
            except Exception as exc:                # never fatal for Core
                self._teardown()
                self._fail(getattr(exc, 'code', None) or 'TUNNEL_START_FAILED')
                return self.status()
            threading.Thread(target=self._watch_start, daemon=True, name='ego-tunnel-start').start()
            return self.status()

    def stop(self):
        with self._lock:
            self._teardown()
            self._state, self._error, self._url, self._words, self._started = 'off', None, None, None, None
            return self.status()

    # ---- internals ----------------------------------------------------------------------------
    def _fail(self, code):
        self._state, self._error, self._url, self._words = 'error', code, None, None

    def _listen(self, gate):
        import uvicorn
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == 'nt':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', 0))
        sock.listen(64)
        self._socket = sock
        # proxy_headers=False: X-Forwarded-For must never turn a visitor into «this PC».
        server = uvicorn.Server(uvicorn.Config(gate, proxy_headers=False, server_header=False, log_config=None,
                                               log_level='critical', access_log=False, timeout_graceful_shutdown=3,
                                               limit_concurrency=32, lifespan='off'))
        self._server = server
        self._thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True, name='ego-tunnel-http')
        self._thread.start()

    def _spawn(self):
        port = self._socket.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        command = (self._command(origin) if self._command else
                   [str(self.binary), 'tunnel', '--no-autoupdate', '--url', origin])
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, creationflags=flags)
        threading.Thread(target=self._read, args=(self._process,), daemon=True, name='ego-tunnel-log').start()

    def _read(self, process):
        # cloudflared logs to stderr; the public address appears once, then keep draining.
        for raw in iter(process.stderr.readline, b''):
            match = URL_RE.search(raw.decode('utf-8', 'replace'))
            if match:
                with self._lock:
                    if self._process is process and self._state == 'starting':
                        self._url, self._state, self._started = match.group(0), 'on', time.time()
        try:
            process.stderr.close()
        except OSError:
            pass
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            pass
        with self._lock:
            if self._process is process and self._state in ('starting', 'on'):
                self._teardown()
                self._fail('TUNNEL_STOPPED')

    def _watch_start(self):
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._state != 'starting':
                    return
            time.sleep(.2)
        with self._lock:
            if self._state == 'starting':
                self._teardown()
                self._fail('TUNNEL_START_FAILED')

    def _teardown(self):
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
        server, self._server = self._server, None
        if server is not None:
            server.should_exit = True
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(5)
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
