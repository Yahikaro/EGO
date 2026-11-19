"""Stage 4b: every error code Core can produce has a human message; API bodies carry it."""
import re
from pathlib import Path

from server.ego_core.errors import CATALOG, describe
from test_core_api import core  # noqa: F401  (fixture reuse)

ROOT = Path(__file__).resolve().parents[2]
RAISED = re.compile(r"(?:CoreFailure|StoreFailure|RuntimeFailure|StorageFailure|ControlFailure)\('([A-Z][A-Z0-9_]+)'")
EXTRA = {'SERVER_RESTARTED', 'CONTENT_BLOCKED', 'INVALID_REQUEST', 'INTERNAL_ERROR', 'REQUEST_TOO_LARGE',
         'INVALID_EVENT_CURSOR', 'RUNTIME_INTERNAL_ERROR', 'CONTEXT_BUDGET_EXCEEDED'}


def test_every_core_and_runtime_code_is_in_the_catalog():
    codes = set(EXTRA)
    for folder in ('server/ego_core', 'runtime/adapters'):
        for path in (ROOT / folder).rglob('*.py'):
            codes |= set(RAISED.findall(path.read_text(encoding='utf-8')))
    missing = sorted(codes - set(CATALOG))
    assert not missing, f'codes without a human message: {missing}'
    for code, entry in CATALOG.items():
        assert entry['message'] and entry['hint'] and entry['message_en'] and entry['hint_en'], code
        assert code not in entry['message'], 'messages explain, they do not repeat the code'


def test_unknown_code_gets_a_safe_generic_text():
    message, hint = describe('SOMETHING_NEW')
    assert 'SOMETHING_NEW' in message and hint


def test_api_error_bodies_carry_catalog_texts_and_the_catalog_route(core):
    client, *_ = core
    body = client.get('/v1/chats/not-a-uuid/messages').json()
    assert body['code'] == 'INVALID_ID'
    assert body['message'] == CATALOG['INVALID_ID']['message'] and body['hint'] == CATALOG['INVALID_ID']['hint']
    catalog = client.get('/v1/errors').json()['errors']
    assert catalog['GENERATION_BUSY']['message'] == CATALOG['GENERATION_BUSY']['message']
    assert client.get('/v1/errors', headers={'authorization': 'Bearer wrong'}).status_code == 401


def test_error_code_document_is_current():
    from scripts.export_error_codes import render
    assert (ROOT / 'docs/ERROR_CODES.md').read_text(encoding='utf-8') == render()
