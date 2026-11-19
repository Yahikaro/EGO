"""Playground cards move from the plaintext registry (QSettings) into the encrypted Core store."""
import json
import secrets

import pytest

from server.ego_core import control
from server.ego_core.api import create_app
from server.ego_core.service import CoreService
from server.ego_core.store import MainStore
from test_control_window import window  # noqa: F401  (fixture reuse)
from test_core_api import LocalClient
from windows_ui import core_characters as cards

CARDS = json.dumps({'Лира': 'Дерзкая наёмница', 'Ольгерд': 'Старый маг'}, ensure_ascii=False)
PROFILES = json.dumps({'Лира': {'personality': 'Дерзкая наёмница', 'appearance': 'Шрам на щеке',
                                'speech': 'Короткие фразы', 'scenario': 'Таверна', 'description': 'x'}},
                      ensure_ascii=False)


class _Runtime:
    config = None
    def cancel(self): pass
    def shutdown(self): pass


@pytest.fixture
def core_api(tmp_path, monkeypatch):
    """control.request routed into a real Core app with a real SQLCipher store."""
    store = MainStore(tmp_path / 'main.db', secrets.token_bytes(32))
    service = CoreService(store, runtime=_Runtime())
    token = secrets.token_urlsafe(32)
    client = LocalClient(create_app(service, token))
    client.headers['authorization'] = 'Bearer ' + token
    calls = []

    def request(root, method, path, *, payload=None, **kwargs):
        calls.append((method, path))
        response = client.request(method, path, json=payload) if payload is not None else client.request(method, path)
        if not response.is_success:
            raise control.ControlFailure(response.json()['code'])
        return response.json()
    monkeypatch.setattr(control, 'request', request)
    yield store, calls
    store.close()


def test_registry_cards_migrate_into_core_with_all_fields(core_api, tmp_path):
    store, _ = core_api
    assert cards.migrate_registry(tmp_path, CARDS, PROFILES) == 2
    stored = {r['data']['name']: r for r in store.list_records('character')}
    lira = stored['Лира']
    assert lira['id'] == cards.card_id('Лира') and lira['id'].isascii()
    assert lira['data']['appearance'] == 'Шрам на щеке' and lira['data']['behavior'] == 'Короткие фразы'
    assert stored['Ольгерд']['data']['personality'] == 'Старый маг'
    assert cards.migrate_registry(tmp_path, CARDS, PROFILES) == 2          # repeatable, no duplicates
    assert len(store.list_records('character')) == 2


def test_playground_save_keeps_fields_set_by_the_client(core_api, tmp_path):
    store, _ = core_api
    store.put_record('character', 'lira', {'name': 'Лира', 'role': 'Хранительница', 'world': 'Эфир',
                                            'personality': 'Старое', 'tags': ['Мистика']})
    record_id = cards.save_card(tmp_path, 'Лира', {'personality': 'Новое', 'appearance': 'Серебряные глаза',
                                                    'speech': '', 'scenario': ''}, 'lira')
    data = store.get_record('character', record_id)['data']
    assert record_id == 'lira' and data['personality'] == 'Новое' and data['appearance'] == 'Серебряные глаза'
    assert data['role'] == 'Хранительница' and data['world'] == 'Эфир' and data['tags'] == ['Мистика']
    cards.delete_card(tmp_path, 'lira')
    assert store.list_records('character') == []


def spin(app, predicate, seconds=5):
    import time
    until = time.monotonic() + seconds
    while not predicate() and time.monotonic() < until:
        app.processEvents(); time.sleep(.01)
    assert predicate()


def test_window_migrates_registry_then_removes_plaintext_copy(window, core_api):
    value, backend, app = window
    store, _ = core_api
    value.path_preferences.setValue(cards.REGISTRY_CARDS, CARDS)
    value.path_preferences.setValue(cards.REGISTRY_PROFILES, PROFILES)
    value._card_refresh()
    spin(app, lambda: value.play_card_selector.count() == 3)
    assert value.path_preferences.value(cards.REGISTRY_CARDS, '') == ''
    assert value.path_preferences.value(cards.REGISTRY_PROFILES, '') == ''
    assert len(store.list_records('character')) == 2
    value.play_card_selector.setCurrentIndex(value.play_card_selector.findData(cards.card_id('Лира')))
    assert value.play_appearance.toPlainText() == 'Шрам на щеке'


def test_window_keeps_registry_when_core_is_offline(window, monkeypatch):
    value, backend, app = window
    def offline(*args, **kwargs): raise control.ControlFailure('CORE_OFFLINE')
    monkeypatch.setattr(control, 'request', offline)
    value.path_preferences.setValue(cards.REGISTRY_CARDS, CARDS)
    value._card_refresh()
    spin(app, lambda: 'запустите Core' in value.play_state.text())
    assert value.path_preferences.value(cards.REGISTRY_CARDS, '') == CARDS   # nothing lost
