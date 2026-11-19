"""Character card field helpers (validation and the legacy registry format).

Since M1.7 (decision D2) cards are stored in the encrypted EGO Core, see
windows_ui/core_characters.py. These parsers are kept to read and migrate the
old plaintext QSettings values.
"""
from __future__ import annotations
import json


def cards_from_json(raw):
    try:
        obj = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    return {str(name): str(description) for name, description in obj.items()
            if isinstance(name, str) and isinstance(description, str)
            and 0 < len(name) <= 120 and len(description) <= 4000}


def add_card(cards, name, description):
    name, description = name.strip(), description.strip()
    if not name or len(name) > 120 or not description or len(description) > 4000:
        raise ValueError("INVALID_CHARACTER_CARD")
    return {**cards, name: description}


def profile_from_fields(name, personality, appearance='', speech='', scenario=''):
    """Collect optional RP fields into a single bounded system-role description."""
    name=name.strip()
    if not name or len(name)>120:
        raise ValueError('INVALID_CHARACTER_CARD')
    parts=[]
    for label,value in [('Личность и характер',personality),('Внешность',appearance),
                        ('Манера речи',speech),('Стартовая сцена',scenario)]:
        value=value.strip()
        if value: parts.append(f'{label}: {value}')
    description='\n'.join(parts)
    if not description or len(description)>4000:
        raise ValueError('INVALID_CHARACTER_CARD')
    return {'personality':personality.strip(),'appearance':appearance.strip(),
            'speech':speech.strip(),'scenario':scenario.strip(),'description':description}


def profiles_from_json(raw):
    try: result=json.loads(raw or '{}')
    except (ValueError,TypeError): return {}
    if not isinstance(result,dict): return {}
    return {name:val for name,val in result.items() if isinstance(name,str)
            and 0<len(name)<=120 and isinstance(val,dict)
            and isinstance(val.get('description'),str) and len(val['description'])<=4000}
