"""Accounts: the owner adds logins on the PC; devices sign in with email + password; sessions, locks."""
import asyncio
import secrets
import types

import httpx
import pytest

from server.ego_core import accounts as acc
from server.ego_core.accounts import Accounts
from server.ego_core.api import create_app
from server.ego_core.service import CoreFailure
from server.ego_core.store import MainStore

KEY = secrets.token_bytes(32)
EMAIL, PASSWORD = 'Owner@Example.com', 'correct horse battery'
FRIEND, FRIEND_PASSWORD = 'friend@example.com', 'another long secret'


class Clock:
    def __init__(self): self.t = 1_800_000_000.0
    def __call__(self): return self.t


@pytest.fixture
def env(tmp_path):
    store = MainStore(tmp_path / 'main.db', KEY)
    clock = Clock()
    accounts = Accounts(store, clock=clock)
    yield types.SimpleNamespace(store=store, clock=clock, accounts=accounts, path=tmp_path)
    store.close()


def test_add_login_and_secrets_are_only_hashed(env):
    assert env.accounts.state() == {'account_exists': False, 'accounts': 0}
    assert env.accounts.add_user(EMAIL, PASSWORD)['users'][0]['email'] == 'owner@example.com'
    assert env.accounts.state() == {'account_exists': True, 'accounts': 1}
    session = env.accounts.login(EMAIL, PASSWORD, device='Windows · Chrome')
    assert session['scope'] == 'account' and env.accounts.session_user(session['token'])
    env.clock.t += 60
    second = env.accounts.login('owner@example.com', PASSWORD)
    me = env.accounts.me(second['token'])['account']
    assert me['email'] == 'owner@example.com' and len(me['sessions']) == 2
    assert sum(s['current'] for s in me['sessions']) == 1
    assert [j['event'] for j in me['journal']][:2] == ['login', 'login']
    env.store.close()
    raw = b''.join(p.read_bytes() for p in env.path.glob('main.db*'))
    for secret in (PASSWORD, session['token'], 'owner@example.com'):
        assert secret.encode() not in raw
    env.store = MainStore(env.path / 'main.db', KEY)   # reopen for fixture teardown


def test_owner_manages_several_accounts(env):
    env.accounts.add_user(EMAIL, PASSWORD)
    env.accounts.add_user(FRIEND, FRIEND_PASSWORD)
    assert [u['email'] for u in env.accounts.list_users()['users']] == ['owner@example.com', FRIEND]
    with pytest.raises(CoreFailure, match='ACCOUNT_EXISTS'):
        env.accounts.add_user(FRIEND.upper(), FRIEND_PASSWORD)
    with pytest.raises(CoreFailure, match='ACCOUNT_WEAK_PASSWORD'):
        env.accounts.add_user('x@example.com', 'short')
    with pytest.raises(CoreFailure, match='ACCOUNT_INVALID_EMAIL'):
        env.accounts.add_user('not-an-email', PASSWORD)
    token = env.accounts.login(FRIEND, FRIEND_PASSWORD)['token']
    owner_token = env.accounts.login(EMAIL, PASSWORD)['token']
    assert env.accounts.me(token)['account']['email'] == FRIEND            # each session is its own account
    assert env.accounts.me(owner_token)['account']['email'] == 'owner@example.com'
    env.accounts.set_user_password(FRIEND, 'a brand new secret')           # ends the friend's sessions
    assert env.accounts.session_user(token) is None and env.accounts.session_user(owner_token)
    with pytest.raises(CoreFailure, match='ACCOUNT_INVALID_CREDENTIALS'):
        env.accounts.login(FRIEND, FRIEND_PASSWORD)
    env.accounts.login(FRIEND, 'a brand new secret')
    env.accounts.remove_user(FRIEND)
    with pytest.raises(CoreFailure, match='ACCOUNT_INVALID_CREDENTIALS'):
        env.accounts.login(FRIEND, 'a brand new secret')
    with pytest.raises(CoreFailure, match='NOT_FOUND'):
        env.accounts.remove_user(FRIEND)


def test_wrong_passwords_lock_for_an_hour(env):
    env.accounts.add_user(EMAIL, PASSWORD)
    for _ in range(acc.PASSWORD_ATTEMPTS - 1):
        with pytest.raises(CoreFailure, match='ACCOUNT_INVALID_CREDENTIALS'):
            env.accounts.login(EMAIL, 'wrong password!')
    with pytest.raises(CoreFailure, match='ACCOUNT_LOCKED'):
        env.accounts.login(EMAIL, 'wrong password!')
    with pytest.raises(CoreFailure, match='ACCOUNT_LOCKED'):
        env.accounts.login(EMAIL, PASSWORD)
    assert env.accounts.list_users()['users'][0]['locked'] is True
    env.clock.t += acc.PASSWORD_LOCK + 1
    assert env.accounts.login(EMAIL, PASSWORD)['token']
    with pytest.raises(CoreFailure, match='ACCOUNT_INVALID_CREDENTIALS'):
        env.accounts.login('nobody@example.com', PASSWORD)                 # unknown login looks like a wrong password


def test_sessions_slide_expire_and_logout(env):
    env.accounts.add_user(EMAIL, PASSWORD)
    token = env.accounts.login(EMAIL, PASSWORD)['token']
    env.clock.t += acc.SESSION_TTL - 10
    assert env.accounts.session_user(token)          # touched: window extended
    env.clock.t += acc.SESSION_TTL - 10
    assert env.accounts.session_user(token)
    env.clock.t += acc.SESSION_TTL + 1
    assert env.accounts.session_user(token) is None  # idle for the whole window
    a = env.accounts.login(EMAIL, PASSWORD)['token']
    env.clock.t += 60
    b = env.accounts.login(EMAIL, PASSWORD)['token']
    env.accounts.logout(a)
    assert env.accounts.session_user(a) is None and env.accounts.session_user(b)
    env.accounts.logout(b, everywhere=True)
    assert env.accounts.session_user(b) is None


def test_profile_is_per_account_and_validated(env):
    env.accounts.add_user(EMAIL, PASSWORD)
    env.accounts.add_user(FRIEND, FRIEND_PASSWORD)
    owner = env.accounts.login(EMAIL, PASSWORD)['token']
    friend = env.accounts.login(FRIEND, FRIEND_PASSWORD)['token']
    profile = {'display_name': 'Alex', 'nickname': 'mk', 'pronouns': 'he', 'pronouns_text': '',
               'description': 'Likes mysteries', 'accent_colour': 'blue'}
    assert env.accounts.set_profile(profile, owner)['profile'] == profile
    assert env.accounts.me(owner)['account']['profile'] == profile
    assert env.accounts.me(friend)['account']['profile']['display_name'] == ''
    for bad in ({'pronouns': 'x'}, {'display_name': 'x' * 61}, {'unknown': 1}):
        with pytest.raises(CoreFailure, match='INVALID_INPUT'):
            env.accounts.set_profile(bad, owner)


# ---- API -----------------------------------------------------------------------
class Client:
    def __init__(self, app, host='127.0.0.1'):
        self.app, self.host, self.headers = app, host, {}
    def request(self, method, path, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=self.app, client=(self.host, 50000))
            async with httpx.AsyncClient(transport=transport, base_url='https://localhost', headers=self.headers) as c:
                return await c.request(method, path, **kwargs)
        return asyncio.run(run())


@pytest.fixture
def api(env):
    service = types.SimpleNamespace(store=env.store, last_api_error=None, installation_id=None)
    admin = secrets.token_urlsafe(32)
    app = create_app(service, admin, accounts=env.accounts)
    return types.SimpleNamespace(app=app, admin=admin, env=env, service=service)


def test_api_flow_owner_adds_account_and_lan_device_signs_in(api):
    lan = Client(api.app, host='192.168.1.20')
    pc = Client(api.app)
    pc.headers = {'Authorization': 'Bearer ' + api.admin}
    assert lan.request('GET', '/v1/account/state').json()['account_exists'] is False
    assert lan.request('GET', '/v1/accounts/users').status_code == 401        # not from another device
    assert lan.request('POST', '/v1/accounts/users', json={'email': EMAIL, 'password': PASSWORD}).status_code == 401
    r = pc.request('POST', '/v1/accounts/users', json={'email': EMAIL, 'password': PASSWORD})
    assert r.status_code == 201 and r.json()['users'][0]['email'] == 'owner@example.com'
    r = lan.request('POST', '/v1/account/login', json={'email': EMAIL, 'password': 'nope nope nope'})
    assert r.status_code == 401 and r.json()['code'] == 'ACCOUNT_INVALID_CREDENTIALS' and r.json()['message']
    r = lan.request('POST', '/v1/account/login', json={'email': EMAIL, 'password': PASSWORD},
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0) Chrome/130.0'})
    assert r.status_code == 200
    lan.headers = {'Authorization': 'Bearer ' + r.json()['token']}
    me = lan.request('GET', '/v1/account/me').json()['account']
    assert me['sessions'][0]['device'] == 'Windows · Chrome' and me['journal'][0]['address'] == '192.168.1.20'
    assert lan.request('POST', '/v1/client/launch').status_code == 401        # account token is client scope only
    assert lan.request('GET', '/v1/accounts/users').status_code == 401
    assert lan.request('POST', '/v1/system/shutdown', json={'operation_id': 'x' * 36}).status_code == 401
    assert pc.request('POST', '/v1/account/logout-all').json()['sessions'] == 1
    assert lan.request('GET', '/v1/account/me').status_code == 401
    assert pc.request('POST', '/v1/accounts/users/remove', json={'email': EMAIL}).json()['users'] == []
    assert api.service.last_api_error is None                                  # user mistakes are not Core errors


def test_api_rate_limit_and_missing_accounts(api):
    lan = Client(api.app, host='192.168.1.20')
    codes = [lan.request('POST', '/v1/account/login', json={'email': 'x@example.com', 'password': 'whatever12'}).status_code
             for _ in range(acc.RATE_LIMIT + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {401}
    bare = create_app(types.SimpleNamespace(store=None, last_api_error=None), secrets.token_urlsafe(32))
    r = Client(bare).request('GET', '/v1/account/state')
    assert r.status_code == 503 and r.json()['code'] == 'ACCOUNTS_UNAVAILABLE'


def test_sessions_last_seven_days():
    assert acc.SESSION_TTL == 7 * 86400
