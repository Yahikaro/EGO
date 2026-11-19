"""The owner's profile (2026-11-06): one person per installation, shared by the PC and devices
signed in with the account. Name, nickname, how to address them, a private «about me», a profile
colour and an avatar. Only the name and the form of address may reach the model, and only when
the owner allows it; «about me» never does.
"""
from __future__ import annotations

import json

from server.ego_core.service import CoreFailure

PRONOUNS = {'he', 'she', 'they', 'other'}
COLOURS = {'violet', 'blue', 'rose', 'mono', 'teal', 'ember', 'gold', 'emerald', 'ice', 'crimson'}
AVATAR_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
AVATAR_MAX = 2 * 1024 * 1024
DEFAULT = {'display_name': '', 'nickname': '', 'pronouns': 'they', 'pronouns_text': '', 'description': '',
           'accent_colour': 'violet', 'use_in_stories': True}
SCHEMA = ('''CREATE TABLE IF NOT EXISTS owner_profile(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL,
             avatar BLOB, media_type TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',)


def clean(data):
    if not isinstance(data, dict) or set(data) - set(DEFAULT):
        raise CoreFailure('INVALID_INPUT')
    out = dict(DEFAULT)
    for key, limit in (('display_name', 60), ('nickname', 40), ('pronouns_text', 40), ('description', 1000)):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > limit:
            raise CoreFailure('INVALID_INPUT')
        out[key] = value.strip()
    if data.get('pronouns', 'they') not in PRONOUNS or data.get('accent_colour', 'violet') not in COLOURS:
        raise CoreFailure('INVALID_INPUT')
    if type(data.get('use_in_stories', True)) is not bool:
        raise CoreFailure('INVALID_INPUT')
    out.update(pronouns=data.get('pronouns', 'they'), accent_colour=data.get('accent_colour', 'violet'),
               use_in_stories=data.get('use_in_stories', True))
    return out


class OwnerProfile:
    def __init__(self, store):
        self.store = store
        with store._transaction() as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    def get(self):
        with self.store._transaction(write=False) as conn:
            row = conn.execute('SELECT data, avatar IS NOT NULL, updated_at FROM owner_profile WHERE id=1').fetchone()
        data = dict(DEFAULT)
        if row:
            try:
                data.update(json.loads(row[0]))
            except ValueError:
                pass
        return {'profile': data, 'has_avatar': bool(row and row[1]), 'updated_at': row[2] if row else None}

    def set(self, data):
        profile = clean(data)
        with self.store._transaction() as conn:
            conn.execute('''INSERT INTO owner_profile(id, data) VALUES(1, ?) ON CONFLICT(id)
                DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP''', (json.dumps(profile, ensure_ascii=False),))
        return self.get()

    def avatar(self):
        with self.store._transaction(write=False) as conn:
            row = conn.execute('SELECT avatar, media_type FROM owner_profile WHERE id=1').fetchone()
        if not row or row[0] is None:
            raise CoreFailure('NOT_FOUND')
        return bytes(row[0]), row[1]

    def set_avatar(self, data, media_type):
        media_type = (media_type or '').split(';')[0].strip().lower()
        signatures = {'image/jpeg': b'\xff\xd8\xff', 'image/png': b'\x89PNG', 'image/webp': b'RIFF'}
        if media_type not in AVATAR_TYPES or not data or len(data) > AVATAR_MAX or not data.startswith(signatures[media_type]):
            raise CoreFailure('INVALID_INPUT')
        with self.store._transaction() as conn:
            conn.execute('''INSERT INTO owner_profile(id, data, avatar, media_type) VALUES(1, ?, ?, ?) ON CONFLICT(id)
                DO UPDATE SET avatar=excluded.avatar, media_type=excluded.media_type, updated_at=CURRENT_TIMESTAMP''',
                         (json.dumps(DEFAULT, ensure_ascii=False), data, media_type))
        return self.get()

    def delete_avatar(self):
        with self.store._transaction() as conn:
            conn.execute('UPDATE owner_profile SET avatar=NULL, media_type=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=1')
        return self.get()

    def persona(self):
        """The only profile facts the model may see: name and form of address (if allowed)."""
        try:
            data = self.get()['profile']
        except Exception:
            return ''
        if not data.get('use_in_stories') or not (data.get('display_name') or data.get('nickname')):
            return ''
        name = data.get('display_name') or data.get('nickname')
        pron = data.get('pronouns')
        his = {'he': 'его', 'she': 'её'}.get(pron, 'его или её')
        forms = {'he': 'в мужском роде', 'she': 'в женском роде', 'they': 'нейтрально, не указывая род'}
        address = forms.get(pron, '')
        if pron == 'other' and data.get('pronouns_text'):
            address = 'так, как просит: «' + data['pronouns_text'][:40] + '»'
        line = f'Собеседника зовут {name}.'
        if data.get('nickname') and data.get('display_name') and data['nickname'] != data['display_name']:
            line += f' Близкие зовут {his} «{data["nickname"]}».'
        if address:
            line += f' Говори о собеседнике и обращайся к нему {address}.'
        return 'О собеседнике (используй имя естественно, не в каждой реплике): ' + line
