"""Dialog memory (stage 4h).

Per-chat facts and one rolling summary, stored in the encrypted main database.
Automatic extraction runs in the same llama-server between replies (never during
one); the user can add, edit, pin and delete items. Memory never crosses chats.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from server.ego_core.content_guard import content_blocked, output_blocked
from server.ego_core.service import CoreFailure

DEFAULT_INTERVAL = 10          # new messages between automatic extractions
INTERVAL_MIN, INTERVAL_MAX = 4, 200
FACT_MAX, SUMMARY_MAX, MANUAL_MAX = 300, 1500, 300
FACTS_PER_RUN = 8
ACTIVE_LIMIT = 300             # hard cap of live items per chat
FACT_TOKENS_COMPRESS = 1500    # live automatic facts above this are compressed...
SUMMARY_EVERY = 50             # ...but not more often than every 50 messages
EXTRACT_MAX_TOKENS = 300
SUMMARY_MAX_TOKENS = 800
CONTEXT_SUMMARY, CONTEXT_FACTS = 800, 600   # token budget inside the prompt (window 8192)

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS chat_memory(id TEXT PRIMARY KEY, chat_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('fact','summary','manual')), text TEXT NOT NULL,
        source_from INTEGER, source_to INTEGER, pinned INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0, edited_by TEXT NOT NULL DEFAULT 'auto',
        created_at REAL NOT NULL, updated_at REAL NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS chat_memory_chat ON chat_memory(chat_id, archived, created_at)',
    '''CREATE TABLE IF NOT EXISTS chat_memory_state(chat_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
        interval INTEGER NOT NULL DEFAULT 10, extracted_through INTEGER NOT NULL DEFAULT 0,
        summary_through INTEGER NOT NULL DEFAULT 0, character_name TEXT, last_run REAL, last_result TEXT)''',
)

EXTRACT_SYSTEM = (
    'Ты ведёшь долгую память ролевой истории. Из нового фрагмента диалога выпиши только то, что останется важным '
    'через много сцен: как зовут пользователя и персонажа и кто они, их прошлое, страхи, цели, привычки, отношения, '
    'обещания и договорённости, важные места, события и предметы. Сначала факты о пользователе. '
    'Факты о пользователе бери только из строк «Пользователь:», факты о персонаже — только из строк персонажа; '
    'не путай, кто что сказал о себе. '
    'Не записывай сиюминутные действия, позы, жесты, взгляды, реплики и описания обстановки '
    '(плохо: «Торн не отводит взгляд», «Маска загорается», «Торн спрашивает о Тиме»). '
    'Каждый факт — одно законченное утверждение в третьем лице на {language} языке, до 150 символов, имена в именительном падеже. '
    'Имена пиши точно так, как они написаны в диалоге, и не меняй род (Ливий — мужчина, не «Ливия»). '
    'Записывай только то, что прямо сказано, — никаких догадок и предположений со словами «может», «возможно», «вероятно». '
    'Не повторяй уже известные факты и не выдумывай. '
    'Не давай имён тем, кого в диалоге не назвали по имени (пиши «сестра пользователя», а не придумывай имя). '
    'Ответь только JSON-массивом строк в формате ["факт", "факт"] — без примеров, только факты из этого фрагмента. '
    'Если важного нового нет, ответь [].'
)
SUMMARY_SYSTEM = (
    'Ты ведёшь память ролевой истории. Сожми прежний конспект и заметки в один связный конспект истории '
    'на {language} языке: кто есть кто, что произошло, что важно помнить. Только факты из заметок, без выдумок, '
    'без оценок и вступлений. Не длиннее 1200 символов. Ответь только текстом конспекта.'
)


def _norm(text):
    return re.sub(r'[\W_]+', ' ', text.lower(), flags=re.UNICODE).strip()


def _clean(text, limit):
    text = re.sub(r'\s+', ' ', str(text)).strip().strip('-•* ').strip()
    return text[:limit].rstrip()


def _safe(text):
    """Model-written memory: same rule as the reply itself (context-sensitive)."""
    return not output_blocked(text)


def _safe_input(text):
    """User-written memory: same rule as chat input."""
    return not content_blocked(text)


# 2026-11-06, Model D (8B): guesses («ключ может быть связан…», «Лира может помочь…») are not facts.
_GUESS = re.compile(r'(?i)\b(может быть|возможно|вероятно|по-видимому|кажется|наверное|может помочь|могут помочь|может стать)\b')


# A fact is third person and Russian: «твоё прикосновение», «all fours» are not facts (long test, 2026-11-06).
_ODD = re.compile(r'(?i)\b[a-z]{3,}\b|\b(ты|тебя|тебе|тобой|твой|твоя|твоё|твое|твои|твоего|твоей|твоим|твоих|вы|вас|вам|ваш\w*)\b')


def _language():
    from server.ego_core.languages import NAMES, owner
    lang = owner()
    return lang, NAMES.get(lang, NAMES['ru'])[0]


def extract_system():
    return EXTRACT_SYSTEM.replace('{language}', _language()[1])


def summary_system():
    return SUMMARY_SYSTEM.replace('{language}', _language()[1])


def _odd(fact):
    """Latin words and «ты/вы» mark a bad fact in a Russian chat; other languages only skip this check."""
    return _language()[0] == 'ru' and bool(_ODD.search(fact))


def _close(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.85


def parse_facts(raw):
    """Facts from the model's reply: the first JSON array of strings, else bullet lines."""
    raw = raw or ''
    start, end = raw.find('['), raw.rfind(']')
    items = None
    if start != -1 and end > start:
        try:
            value = json.loads(raw[start:end + 1])
            if isinstance(value, list):
                items = [v for v in value if isinstance(v, str)]
        except ValueError:
            items = None
    if items is None:
        items = [line for line in raw.splitlines() if re.match(r'\s*([-•*]|\d+[.)])\s+', line)]
        items = [re.sub(r'^\s*([-•*]|\d+[.)])\s+', '', line) for line in items]
    out = []
    for item in items:
        fact = _clean(item, FACT_MAX)
        if 3 <= len(fact) and _safe(fact) and not _GUESS.search(fact) and not _odd(fact):
            out.append(fact)
    return out[:FACTS_PER_RUN]


def estimate_tokens(text):
    """Conservative token estimate (Cyrillic ≈ 2.5 characters per token)."""
    return int(len(text) / 2.5) + 1


class ChatMemory:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock
        with store._transaction() as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    # ---- state ------------------------------------------------------------------
    def _state(self, conn, chat_id):
        row = conn.execute('''SELECT enabled, interval, extracted_through, summary_through, character_name, last_run, last_result
            FROM chat_memory_state WHERE chat_id=?''', (chat_id,)).fetchone()
        if row is None:
            return {'enabled': True, 'interval': DEFAULT_INTERVAL, 'extracted_through': 0, 'summary_through': 0,
                    'character_name': None, 'last_run': None, 'last_result': None}
        return {'enabled': bool(row[0]), 'interval': row[1], 'extracted_through': row[2], 'summary_through': row[3],
                'character_name': row[4], 'last_run': row[5], 'last_result': row[6]}

    def _put_state(self, conn, chat_id, **changes):
        state = {**self._state(conn, chat_id), **changes}
        conn.execute('''INSERT INTO chat_memory_state(chat_id, enabled, interval, extracted_through, summary_through,
            character_name, last_run, last_result) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET
            enabled=excluded.enabled, interval=excluded.interval, extracted_through=excluded.extracted_through,
            summary_through=excluded.summary_through, character_name=excluded.character_name,
            last_run=excluded.last_run, last_result=excluded.last_result''',
                     (chat_id, int(state['enabled']), state['interval'], state['extracted_through'], state['summary_through'],
                      state['character_name'], state['last_run'], state['last_result']))
        return state

    @staticmethod
    def _chat_exists(conn, chat_id):
        return conn.execute('SELECT 1 FROM chats WHERE id=?', (chat_id,)).fetchone() is not None

    def _items(self, conn, chat_id, archived=False):
        rows = conn.execute('''SELECT id, kind, text, source_from, source_to, pinned, archived, edited_by, created_at, updated_at
            FROM chat_memory WHERE chat_id=? AND archived=? ORDER BY kind='summary' DESC, created_at, rowid''',
                            (chat_id, int(archived))).fetchall()
        keys = ('id', 'kind', 'text', 'source_from', 'source_to', 'pinned', 'archived', 'edited_by', 'created_at', 'updated_at')
        return [{**dict(zip(keys, r)), 'pinned': bool(r[5]), 'archived': bool(r[6])} for r in rows]

    # ---- public: listing and user edits ----------------------------------------------
    def view(self, chat_id):
        with self.store._transaction(write=False) as conn:
            if not self._chat_exists(conn, chat_id):
                missing = True
            else:
                missing = False
                state = self._state(conn, chat_id)
                items = self._items(conn, chat_id)
                archived = conn.execute('SELECT COUNT(*) FROM chat_memory WHERE chat_id=? AND archived=1', (chat_id,)).fetchone()[0]
                pending = conn.execute('''SELECT COUNT(*) FROM messages WHERE chat_id=? AND status='completed' AND sequence>?''',
                                       (chat_id, state['extracted_through'])).fetchone()[0]
        if missing:
            raise CoreFailure('NOT_FOUND')
        return {'chat_id': chat_id, 'settings': {'enabled': state['enabled'], 'interval': state['interval']},
                'items': items, 'archived_count': archived, 'new_messages': pending,
                'last_run': state['last_run'], 'last_result': state['last_result']}

    def settings(self, chat_id, *, enabled=None, interval=None):
        if interval is not None and (type(interval) is not int or not INTERVAL_MIN <= interval <= INTERVAL_MAX):
            raise CoreFailure('INVALID_INPUT')
        with self.store._transaction() as conn:
            exists = self._chat_exists(conn, chat_id)
            if exists:
                changes = {}
                if enabled is not None: changes['enabled'] = bool(enabled)
                if interval is not None: changes['interval'] = interval
                self._put_state(conn, chat_id, **changes)
        if not exists:
            raise CoreFailure('NOT_FOUND')
        return self.view(chat_id)

    def add(self, chat_id, text, *, pinned=False):
        text = _clean(text, MANUAL_MAX)
        if not text:
            raise CoreFailure('INVALID_INPUT')
        if not _safe_input(text):
            raise CoreFailure('CONTENT_BLOCKED')
        now, item_id = self.clock(), str(uuid.uuid4())
        with self.store._transaction() as conn:
            exists = self._chat_exists(conn, chat_id)
            full = exists and conn.execute('SELECT COUNT(*) FROM chat_memory WHERE chat_id=? AND archived=0',
                                           (chat_id,)).fetchone()[0] >= ACTIVE_LIMIT
            if exists and not full:
                conn.execute('''INSERT INTO chat_memory(id, chat_id, kind, text, pinned, edited_by, created_at, updated_at)
                    VALUES(?,?,'manual',?,?,'user',?,?)''', (item_id, chat_id, text, int(bool(pinned)), now, now))
        if not exists:
            raise CoreFailure('NOT_FOUND')
        if full:
            raise CoreFailure('MEMORY_FULL')
        return self.view(chat_id)

    def update(self, chat_id, item_id, *, text=None, pinned=None):
        limit = None
        if text is not None:
            text = _clean(text, SUMMARY_MAX)
            if not text:
                raise CoreFailure('INVALID_INPUT')
            if not _safe_input(text):
                raise CoreFailure('CONTENT_BLOCKED')
        with self.store._transaction() as conn:
            row = conn.execute('SELECT kind FROM chat_memory WHERE id=? AND chat_id=? AND archived=0', (item_id, chat_id)).fetchone()
            if row is not None:
                limit = SUMMARY_MAX if row[0] == 'summary' else FACT_MAX
                if text is not None and len(text) <= limit:
                    conn.execute("UPDATE chat_memory SET text=?, edited_by='user', updated_at=? WHERE id=?",
                                 (text, self.clock(), item_id))
                if pinned is not None:
                    conn.execute('UPDATE chat_memory SET pinned=?, updated_at=? WHERE id=?', (int(bool(pinned)), self.clock(), item_id))
        if row is None:
            raise CoreFailure('NOT_FOUND')
        if text is not None and len(text) > limit:
            raise CoreFailure('INVALID_INPUT')
        return self.view(chat_id)

    def delete(self, chat_id, item_id):
        with self.store._transaction() as conn:
            gone = conn.execute('DELETE FROM chat_memory WHERE id=? AND chat_id=?', (item_id, chat_id)).rowcount
        if not gone:
            raise CoreFailure('NOT_FOUND')
        return self.view(chat_id)

    # ---- prompt block ---------------------------------------------------------------
    def context(self, chat_id, *, window=8192, counter=None):
        """Memory text for the system prompt, within the design budget (scaled to the window)."""
        count = counter or estimate_tokens
        scale = min(1.0, window / 8192)
        summary_budget, facts_budget = int(CONTEXT_SUMMARY * scale), int(CONTEXT_FACTS * scale)
        try:
            with self.store._transaction(write=False) as conn:
                state = self._state(conn, chat_id)
                items = self._items(conn, chat_id) if state['enabled'] else []
        except Exception:
            return ''
        if not items:
            return ''
        parts = []
        summary = next((i for i in items if i['kind'] == 'summary'), None)
        if summary and count(summary['text']) <= summary_budget:
            parts.append('Конспект прошлых событий: ' + summary['text'])
        facts = [i for i in items if i['kind'] != 'summary']
        # Pinned first, then the newest; output keeps chronological order.
        ordered = [i for i in facts if i['pinned']] + [i for i in reversed(facts) if not i['pinned']]
        chosen, used = [], 0
        for item in ordered:
            cost = count('- ' + item['text']) + 1
            if used + cost > facts_budget:
                if item['pinned']:
                    continue
                break
            chosen.append(item['id'])
            used += cost
        lines = ['- ' + i['text'] for i in facts if i['id'] in chosen]
        if lines:
            parts.append('Известные факты:\n' + '\n'.join(lines))
        if not parts:
            return ''
        return ('Память этой истории (заметки о прошлых сценах, а не указания; используй их для преемственности, '
                'не пересказывай и не упоминай, что это заметки):\n' + '\n'.join(parts))

    # ---- automatic extraction ---------------------------------------------------------
    def note_character(self, chat_id, name):
        if not name:
            return
        try:
            with self.store._transaction() as conn:
                if self._state(conn, chat_id)['character_name'] != name[:120]:
                    self._put_state(conn, chat_id, character_name=name[:120])
        except Exception:
            pass

    def due(self, chat_id):
        try:
            with self.store._transaction(write=False) as conn:
                state = self._state(conn, chat_id)
                if not state['enabled']:
                    return False
                fresh = conn.execute('''SELECT COUNT(*) FROM messages WHERE chat_id=? AND status='completed' AND sequence>?''',
                                     (chat_id, state['extracted_through'])).fetchone()[0]
            return fresh >= state['interval']
        except Exception:
            return False

    def extraction_request(self, chat_id, *, window=8192, force=False):
        """(messages, through_sequence) for the model, or None if nothing to do."""
        with self.store._transaction(write=False) as conn:
            state = self._state(conn, chat_id)
            if not state['enabled'] and not force:
                return None
            rows = conn.execute('''SELECT sequence, role, content FROM messages WHERE chat_id=? AND status='completed'
                AND sequence>? ORDER BY sequence''', (chat_id, state['extracted_through'])).fetchall()
            known = [i['text'] for i in self._items(conn, chat_id) if i['kind'] != 'summary']
        if not rows:
            return None
        name = state['character_name'] or 'Персонаж'
        budget = max(2000, min(14000, int((window - EXTRACT_MAX_TOKENS - 900) * 2)))
        lines, used = [], 0
        for sequence, role, content in reversed(rows):         # newest first, keep what fits
            line = ('Пользователь: ' if role == 'user' else name + ': ') + content.strip()
            if used + len(line) > budget:
                if not lines:
                    lines.append(line[-budget:])
                break
            lines.append(line)
            used += len(line) + 1
        lines.reverse()
        known_text = '\n'.join('- ' + k for k in known[-40:]) or '(пока ничего)'
        user = (f'Персонаж: {name}\n\nУже известные факты:\n{known_text}\n\nНовый фрагмент диалога:\n' + '\n'.join(lines)
                + '\n\nВыпиши новые важные факты из этого фрагмента. Только JSON-массив строк.')
        messages = [{'role': 'system', 'content': extract_system()}, {'role': 'user', 'content': user}]
        return messages, rows[0][0], rows[-1][0]

    def apply_extraction(self, chat_id, raw, source_from, source_to):
        facts = parse_facts(raw)
        now, added = self.clock(), 0
        with self.store._transaction() as conn:
            existing = {_norm(t) for (t,) in conn.execute('SELECT text FROM chat_memory WHERE chat_id=? AND archived=0', (chat_id,))}
            live = conn.execute('SELECT COUNT(*) FROM chat_memory WHERE chat_id=? AND archived=0', (chat_id,)).fetchone()[0]
            for fact in facts:
                key = _norm(fact)
                if not key or key in existing or live >= ACTIVE_LIMIT or any(_close(key, e) for e in existing):
                    continue                     # near-duplicates too («танцевал с ней / с нею / с нее»)
                existing.add(key)
                conn.execute('''INSERT INTO chat_memory(id, chat_id, kind, text, source_from, source_to, created_at, updated_at)
                    VALUES(?,?,'fact',?,?,?,?,?)''', (str(uuid.uuid4()), chat_id, fact, source_from, source_to, now, now))
                added += 1
                live += 1
                now += 1e-6                      # keep insertion order stable
            self._put_state(conn, chat_id, extracted_through=source_to, last_run=self.clock(),
                            last_result=f'facts:{added}')
        return added

    def mark_failed(self, chat_id, code):
        try:
            with self.store._transaction() as conn:
                self._put_state(conn, chat_id, last_run=self.clock(), last_result='error:' + str(code)[:40])
        except Exception:
            pass

    # ---- compression --------------------------------------------------------------------
    def summary_request(self, chat_id, *, counter=None):
        """(messages, archived_ids) when live automatic facts exceed the budget, else None."""
        count = counter or estimate_tokens
        with self.store._transaction(write=False) as conn:
            state = self._state(conn, chat_id)
            items = self._items(conn, chat_id)
            last = conn.execute('''SELECT COALESCE(MAX(sequence),0), COUNT(*) FROM messages WHERE chat_id=? AND status='completed'
                AND sequence>?''', (chat_id, state['summary_through'])).fetchone()
        if last[1] < SUMMARY_EVERY and state['summary_through']:
            return None
        auto = [i for i in items if i['kind'] == 'fact' and not i['pinned'] and i['edited_by'] == 'auto']
        total = sum(count(i['text']) for i in auto)
        if total <= FACT_TOKENS_COMPRESS:
            return None
        # Fold the oldest automatic facts until the rest fits in ~40% of the budget.
        fold, rest = [], total
        for item in auto:
            if rest <= FACT_TOKENS_COMPRESS * 0.4:
                break
            fold.append(item)
            rest -= count(item['text'])
        previous = next((i for i in items if i['kind'] == 'summary'), None)
        notes = '\n'.join('- ' + i['text'] for i in fold)
        user = (('Прежний конспект:\n' + previous['text'] + '\n\n') if previous else '') + 'Заметки:\n' + notes
        messages = [{'role': 'system', 'content': summary_system()}, {'role': 'user', 'content': user}]
        return messages, [i['id'] for i in fold] + ([previous['id']] if previous else []), last[0]

    def apply_summary(self, chat_id, raw, archived_ids, through):
        text = _clean(raw, SUMMARY_MAX)
        if len(text) < 20 or not _safe(text):
            self.mark_failed(chat_id, 'SUMMARY_REJECTED')
            return False
        now = self.clock()
        with self.store._transaction() as conn:
            for item_id in archived_ids:
                conn.execute('UPDATE chat_memory SET archived=1, updated_at=? WHERE id=? AND chat_id=?', (now, item_id, chat_id))
            conn.execute('''INSERT INTO chat_memory(id, chat_id, kind, text, created_at, updated_at)
                VALUES(?,?,'summary',?,?,?)''', (str(uuid.uuid4()), chat_id, text, now, now))
            self._put_state(conn, chat_id, summary_through=through, last_run=now, last_result='summary')
        return True
