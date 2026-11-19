"""Languages (2026-11-06): error texts and emails follow the client's language."""
from server.ego_core import languages
from server.ego_core.errors import CATALOG, describe
from server.ego_core.errors_i18n import TEXTS
from test_core_api import core  # noqa: F401  (fixture reuse)


def test_every_error_code_has_french_spanish_and_german_texts():
    for lang in ('fr', 'es', 'de'):
        assert set(TEXTS[lang]) == set(CATALOG), lang
        for code, (message, hint) in TEXTS[lang].items():
            assert message.strip() and hint.strip(), (lang, code)
    assert describe('CORE_BUSY', 'de')[0].startswith('Das Modell')
    assert describe('CORE_BUSY', 'en') == (CATALOG['CORE_BUSY']['message_en'], CATALOG['CORE_BUSY']['hint_en'])
    assert describe('CORE_BUSY') == (CATALOG['CORE_BUSY']['message'], CATALOG['CORE_BUSY']['hint'])


def test_request_language_from_headers():
    assert languages.from_headers('fr', 'de-DE') == 'fr'
    assert languages.from_headers(None, 'es-MX,es;q=0.9') == 'es'
    assert languages.from_headers('xx', 'zz-ZZ') == 'ru'
    token = languages.use('de')
    try:
        assert describe('NOT_FOUND')[0] == TEXTS['de']['NOT_FOUND'][0]
    finally:
        languages.reset(token)
    assert languages.current() == 'ru'


def test_api_error_body_follows_x_ego_lang(core):
    client, *_ = core
    default = client.get('/v1/chats/not-a-uuid/messages').json()
    assert default['message'] == CATALOG['INVALID_ID']['message']
    for lang in ('en', 'fr', 'es', 'de'):
        body = client.get('/v1/chats/not-a-uuid/messages', headers={'X-EGO-Lang': lang}).json()
        assert body['code'] == 'INVALID_ID'
        assert body['message'] == describe('INVALID_ID', lang)[0]
    body = client.get('/v1/chats/not-a-uuid/messages', headers={'Accept-Language': 'es-ES,es;q=0.9'}).json()
    assert body['message'] == TEXTS['es']['INVALID_ID'][0]


def test_model_writes_in_the_owner_language(monkeypatch):
    from server.ego_core import memory as mem
    from server.ego_core.roleplay_prompt import RULE_LANGUAGE_RU, roleplay_system
    ru = roleplay_system('Мара', 'Профиль')
    assert RULE_LANGUAGE_RU in ru and 'Reply only' not in ru
    monkeypatch.setattr(languages, '_owner', 'en')
    en = roleplay_system('Mara', 'Профиль')
    assert RULE_LANGUAGE_RU not in en and 'английском языке (English)' in en and en.endswith('Reply only in English.')
    assert 'на английском языке' in mem.extract_system() and 'на английском языке' in mem.summary_system()
    assert mem.parse_facts('["Alex is a sailor from the north."]') == ['Alex is a sailor from the north.']
    monkeypatch.setattr(languages, '_owner', 'ru')
    assert 'на русском языке' in mem.extract_system()
    assert mem.parse_facts('["Alex is a sailor from the north."]') == []


def test_client_header_sets_the_owner_language(core, monkeypatch):
    client, *_ = core
    monkeypatch.setattr(languages, '_owner', 'ru')
    client.get('/v1/chats/not-a-uuid/messages', headers={'Accept-Language': 'de'})
    assert languages.owner() == 'ru'          # only the client's own header changes it
    client.get('/v1/chats/not-a-uuid/messages', headers={'X-EGO-Lang': 'fr'})
    assert languages.owner() == 'fr'


def test_every_client_text_has_a_translation():
    """Run scripts/i18n_extract.py + i18n_build.py after changing client texts."""
    from scripts.i18n_build import GLUED, load
    from scripts.i18n_extract import collect
    rows = load()
    missing = sorted(k for k in collect() if k not in rows and k not in GLUED)
    assert not missing, missing[:10]
