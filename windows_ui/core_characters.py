"""Playground character cards stored in the encrypted EGO Core (decision D2).

Cards used to live as plaintext JSON in the Windows registry (QSettings). They are
now ordinary Core characters, shared with the EGO client. The playground edits
only its four fields and keeps every other field the client may have set.
Runs in worker threads; needs a running Core. Never logs card contents.
"""
from __future__ import annotations

import hashlib

from urllib.parse import quote

from server.ego_core import control

REGISTRY_CARDS = 'character_cards_v1'
REGISTRY_PROFILES = 'character_profiles_v2'

# Playground field -> Core character field (names follow the client's studio).
FIELDS = {'personality': 'personality', 'appearance': 'appearance', 'speech': 'behavior', 'scenario': 'scenario'}


def card_id(name):
    """Stable ASCII id for a card name (Cyrillic names included)."""
    return 'gui-' + hashlib.sha256(name.strip().encode('utf-8')).hexdigest()[:16]


def to_form(data):
    """Core character -> playground fields. Older client cards keep their description visible."""
    form = {key: str(data.get(field) or '') for key, field in FIELDS.items()}
    if not form['personality']:
        form['personality'] = str(data.get('description') or '')
    return form


def list_cards(root):
    return control.request(root, 'GET', '/v1/characters', timeout=10)['characters']


def save_card(root, name, fields, record_id=None):
    """Create or update a card; returns its id. Keeps fields the playground does not edit."""
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError('INVALID_CHARACTER_CARD')
    record_id = record_id or card_id(name)
    current = {}
    for record in list_cards(root):
        if record['id'] == record_id:
            current = record['data']
            break
    data = {**current, 'name': name, 'initial': name[:1].upper()}
    data.setdefault('colour', 'lira')
    data.setdefault('source', 'server-gui')
    for key, field in FIELDS.items():
        data[field] = str(fields.get(key) or '').strip()
    control.request(root, 'PUT', f'/v1/characters/{quote(record_id)}', payload={'data': data}, timeout=10)
    return record_id


def delete_card(root, record_id):
    control.request(root, 'DELETE', f'/v1/characters/{quote(record_id)}', timeout=10)


def registry_cards(cards_raw, profiles_raw):
    """Parse the old registry values into {name: form fields}."""
    from windows_ui.character_cards import cards_from_json, profiles_from_json
    cards = cards_from_json(cards_raw)
    profiles = profiles_from_json(profiles_raw)
    result = {}
    for name, personality in cards.items():
        profile = profiles.get(name, {})
        result[name] = {'personality': profile.get('personality', personality),
                        'appearance': profile.get('appearance', ''),
                        'speech': profile.get('speech', ''),
                        'scenario': profile.get('scenario', '')}
    return result


def migrate_registry(root, cards_raw, profiles_raw):
    """Copy registry cards into Core and verify each by reading it back.

    Returns the number of cards now in Core. Raises if any card could not be
    written or verified, so the caller keeps the registry copy in that case.
    A card whose id already exists in Core is left as it is (Core wins).
    """
    cards = registry_cards(cards_raw, profiles_raw)
    if not cards:
        return 0
    existing = {record['id'] for record in list_cards(root)}
    for name, fields in cards.items():
        if card_id(name) not in existing:
            save_card(root, name, fields)
    stored = {record['id']: record['data'] for record in list_cards(root)}
    for name, fields in cards.items():
        data = stored.get(card_id(name))
        if data is None or data.get('name') != name.strip():
            raise RuntimeError('MIGRATION_NOT_VERIFIED')
        if card_id(name) not in existing and to_form(data) != {k: str(v or '').strip() for k, v in fields.items()}:
            raise RuntimeError('MIGRATION_NOT_VERIFIED')
    return len(cards)


