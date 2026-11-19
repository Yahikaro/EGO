"""Languages of EGO (2026-11-06): ru (default), en, fr, es, de.

The client sends its interface language in the X-EGO-Lang header; Core uses it for
error texts of that request. Without the header, Accept-Language is used,
then Russian (the language the existing installation and tests were written for).
"""
from __future__ import annotations

import contextvars

SUPPORTED = ('ru', 'en', 'fr', 'es', 'de')
DEFAULT = 'ru'
_current = contextvars.ContextVar('ego_language', default=DEFAULT)


def normalize(value):
    value = str(value or '').strip().lower()[:2]
    return value if value in SUPPORTED else None


def from_headers(ego_lang=None, accept_language=None):
    chosen = normalize(ego_lang)
    if chosen:
        return chosen
    for part in str(accept_language or '').split(',')[:8]:
        chosen = normalize(part.split(';')[0])
        if chosen:
            return chosen
    return DEFAULT


def current():
    return _current.get()


def use(lang):
    """Set the language for the current request; returns a token for reset()."""
    return _current.set(normalize(lang) or DEFAULT)


def reset(token):
    _current.reset(token)


# ---- the language the model writes in -----------------------------------------------------------
# One owner per installation: the client's interface language is remembered when it talks to Core
# (X-EGO-Lang) and used for replies, chat memory and the Enchant helper, also in background threads.
_owner = DEFAULT
NAMES = {'ru': ('русском', 'Russian'), 'en': ('английском', 'English'), 'fr': ('французском', 'French'),
         'es': ('испанском', 'Spanish'), 'de': ('немецком', 'German')}
REPLY_ONLY = {'ru': 'Отвечай только на русском языке.', 'en': 'Reply only in English.',
              'fr': 'Réponds uniquement en français.', 'es': 'Responde solo en español.',
              'de': 'Antworte nur auf Deutsch.'}


def set_owner(lang):
    global _owner
    lang = normalize(lang)
    if lang:
        _owner = lang


def owner():
    return _owner
