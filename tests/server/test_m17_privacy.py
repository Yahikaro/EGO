"""Privacy stage: no third-party requests from the client, no story text in browser caches,
strict browser security headers."""
import re
import secrets
import types
from pathlib import Path

from server.ego_core.api import create_app
from test_m17_accounts import Client

CLIENT = Path(__file__).resolve().parents[2] / 'client'


def _app(tmp_path):
    client_dir = tmp_path / 'client'
    client_dir.mkdir()
    (client_dir / 'index.html').write_text('<!doctype html><title>EGO</title>', encoding='utf-8')
    service = types.SimpleNamespace(store=types.SimpleNamespace(list_chats=lambda: []), last_api_error=None, installation_id=None)
    admin = secrets.token_urlsafe(32)
    return create_app(service, admin, client_dir=client_dir), admin


def test_api_responses_are_never_cached_and_carry_security_headers(tmp_path):
    app, admin = _app(tmp_path)
    c = Client(app)
    c.headers = {'Authorization': 'Bearer ' + admin}
    for path in ('/v1/chats', '/health', '/v1/does-not-exist'):
        r = c.request('GET', path)
        assert r.headers['cache-control'] == 'no-store', path
        assert r.headers['x-content-type-options'] == 'nosniff'
        assert r.headers['referrer-policy'] == 'no-referrer'
        assert r.headers['x-frame-options'] == 'DENY'
        assert "frame-ancestors 'none'" in r.headers['content-security-policy']
    page = c.request('GET', '/app/')
    assert page.status_code == 200 and page.headers['cache-control'] == 'no-cache'
    csp = page.headers['content-security-policy']
    assert "script-src 'self';" in csp and "connect-src 'self'" in csp and 'unsafe-eval' not in csp
    assert len([h for h in page.headers.raw if h[0].lower() == b'cache-control']) == 1


def test_client_makes_no_third_party_requests_and_has_no_inline_handlers():
    if not (CLIENT / 'index.html').is_file():
        import pytest
        pytest.skip('client folder missing')
    external = re.compile(r'https?://(?!127\.0\.0\.1|localhost|www\.w3\.org)[a-z0-9.-]+', re.I)
    inline = re.compile(r'\son(?:error|load|click|mouse\w+|key\w+|change|input|submit|focus|blur)\s*=\s*["\']', re.I)
    files = [p for p in [*CLIENT.rglob('*.js'), *CLIENT.rglob('*.css'), *CLIENT.glob('*.html')] if 'tests' not in p.parts]
    assert len(files) > 15
    for path in files:
        text = path.read_text(encoding='utf-8')
        assert not external.search(text), (path.name, external.search(text).group())
        assert not inline.search(text), (path.name, inline.search(text).group())


def test_streaming_responses_keep_no_store(core):  # noqa: F811
    c, *_ = core
    r = c.get('/v1/events')
    assert r.headers['cache-control'] == 'no-store'


from test_core_api import core  # noqa: E402,F401  (fixture reuse)


def test_root_opens_the_client(tmp_path):
    app, _ = _app(tmp_path)
    r = Client(app).request('GET', '/', follow_redirects=False)
    assert r.status_code == 307 and r.headers['location'] == '/app/'
