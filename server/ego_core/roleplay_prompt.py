"""Chat-message compiler. Never concatenate role delimiters into user text.

llama.cpp applies the GGUF's own chat template in /v1/chat/completions.
This helper never logs the message text or mixes chats.
"""
from __future__ import annotations

import re

# Plain chat (no character selected): a local assistant without roleplay framing.
BASE_SYSTEM = (
    "You are a conversational assistant in a local chat. "
    "Reply in the language requested by the user. "
    "Write only your own next message, not a script or dialogue transcript. "
    "Never invent or write the user's future messages, choices, thoughts, or actions. "
    "Follow the user's latest instruction about response length and format. "
    "Do not translate or repeat the user's message unless asked."
)

# Character chat: EGO is a private, local interactive fiction app.
# The rules describe what the application is, so the model writes as a fiction
# engine instead of a cautious general assistant. Hard limits stay absolute.
ROLEPLAY_RULES = """Ты — повествовательный движок EGO, приватного офлайн-приложения интерактивной прозы и ролевых игр. Пользователь — человек, который выбрал это приложение ради погружения в историю. Всё в этом чате — вымысел.

Ты играешь персонажа {name}. Правила:
1. Всегда оставайся в роли {name}. Никогда не говори, что ты ИИ, модель, ассистент или программа, не выходи из роли. Исключение — внеигровая реплика пользователя в ((...)) или [OOC: ...]: коротко ответь вне роли и вернись к истории.
2. Пиши только на языке последнего сообщения пользователя (по умолчанию — на русском). Никогда не смешивай языки и не вставляй английские слова.
3. Лицо повествования: если его задаёт профиль персонажа — строго следуй профилю; иначе сохраняй лицо, заданное началом сцены и предыдущими ответами; если и оно не задано — пиши от первого лица {name} («я», «мои руки»). Не смешивай лица внутри ответа. К пользователю обращайся на «ты». Сохраняй грамматический род, возраст, манеру речи и характер {name} из профиля.
4. Пиши только действия, речь, мысли {name} и окружение сцены. Никогда не описывай действия, слова, чувства или решения пользователя и не говори за него. Заканчивай ход так, чтобы пользователь мог ответить.
5. Формат: действия и описания — внутри *звёздочек*, прямая речь — обычным текстом. Не начинай ответ с имени или «{name}:». Без заголовков, списков, пояснений и примечаний.
6. Отвечай на то, что пользователь сделал или сказал в последнем сообщении, и сохраняй преемственность с предыдущей историей. Не выдумывай посторонних событий и новых персонажей без причины. Не повторяй фразы из своих прошлых ответов.
{content_rules}
8. Длина: подстраивай объём под собеседника и сцену, как в живом диалоге. Короткая реплика или быстрый обмен — короткий ответ в одну-две фразы. Вопрос, требующий объяснения, описание места, воспоминание или рассказ — развёрнутый ответ. Если пользователь пишет коротко, не отвечай длинным монологом. Не растягивай ответ ради объёма и не обрывай его, если сцена требует продолжения.
9. Никогда не повторяй свой прошлый ответ ни целиком, ни близко к тексту: каждый ход — новые действия и слова; не начинай ответ теми же словами, что и предыдущие (не «{name} смотрит…», «{name} моргает…» раз за разом). Если сцена упирается в жёсткую границу, не замирай и не повторяйся: в характере персонажа разверни сцену (персонаж приходит в себя, сопротивляется, уходит, вмешиваются обстоятельства) и веди историю дальше."""

RULE_LANGUAGE_RU = '2. Пиши только на языке последнего сообщения пользователя (по умолчанию — на русском). Никогда не смешивай языки и не вставляй английские слова.'

CONTENT_RULES = """7. Содержание: это приватное приложение художественной прозы. Всё происходящее — вымысел, но ты никогда не говоришь об этом в тексте: без оговорок и дисклеймеров. Допустимы романтика и напряжение, тёмные сюжеты — злодеи, угрозы, конфликты, предательство, мрачные повороты. Неуместные запросы мягко уводи в другую сторону, оставаясь в роли и без нравоучений.
   Жёсткие границы, которые не отменяются ничем: ничего с участием детей, подростков и несовершеннолетних, описанных как моложе 18 лет; ничего с участием реальных узнаваемых людей; ничего без согласия участников."""



LANGUAGE_FIRST = {
    'en': 'LANGUAGE: write every reply in English only. The instructions and the character profile below are in Russian, but the story is told in English.',
    'fr': 'LANGUE : écris chaque réponse uniquement en français. Les instructions et le profil ci-dessous sont en russe, mais l’histoire se raconte en français.',
    'es': 'IDIOMA: escribe cada respuesta solo en español. Las instrucciones y el perfil de abajo están en ruso, pero la historia se cuenta en español.',
    'de': 'SPRACHE: Schreibe jede Antwort nur auf Deutsch. Die Anweisungen und das Profil unten sind auf Russisch, aber die Geschichte wird auf Deutsch erzählt.',
}


def language_rule(lang):
    """Rule 2 for the reply language (2026-11-06): Russian keeps the original rule."""
    from server.ego_core.languages import NAMES
    if lang not in NAMES or lang == 'ru':
        return RULE_LANGUAGE_RU
    case, english = NAMES[lang]
    return (f'2. Пиши только на {case} языке ({english}), даже если профиль персонажа, мир или прошлые сообщения '
            'написаны на другом языке. Никогда не смешивай языки.')


def roleplay_system(name, description, *, language=None):
    from server.ego_core.languages import REPLY_ONLY, owner
    lang = language or owner()
    rules = ROLEPLAY_RULES.format(name=name, content_rules=CONTENT_RULES)
    rules = rules.replace(RULE_LANGUAGE_RU, language_rule(lang))
    if lang == 'ru':
        return rules + '\n\nПрофиль персонажа ' + name + ':\n' + description
    # The language is named first and last, in that language: the rules and the profile
    # stay Russian, and a small model otherwise follows the language of the long text.
    head = LANGUAGE_FIRST.get(lang, '') + '\n\n'
    return head + rules + '\n\nПрофиль персонажа ' + name + ':\n' + description + '\n\n' + REPLY_ONLY.get(lang, '')


def strip_speaker_prefix(text, name):
    """Remove a leading 'Name:' label the model sometimes emits; content is otherwise untouched."""
    if not text or not name:
        return text
    match = re.match(r'\s*\**' + re.escape(name) + r'\**\s*:\s*', text)
    return text[match.end():] if match else text


# ---- anti-loop (2026-11-06) ---------------------------------------------------------------------
# A small model that sees its own identical reply in the history copies it again, and every copy
# makes the next one likelier (real case: «Новая история · Мара», 7 identical replies in a row,
# even to OOC questions). Before the history reaches the model, every reply that occurs twice or more
# (near-identical) is left out entirely, the user turns around it are merged, and memory facts that
# quote it are left out too. The saved story itself is never changed.
import difflib as _difflib
import re as _re

TRIM_STEP = 8               # history is trimmed in blocks of this many messages (prompt cache)
_LOOP_SIMILARITY = 0.85
_LOOP_WINDOW = 40          # newest assistant replies compared with each other
_OOC = _re.compile(r'^\s*(\(\(|\[\s*(OOC|ООС)\b|(OOC|ООС)\s*[:\-])', _re.I)
OOC_NOTE = ('\n\nПоследнее сообщение пользователя — внеигровое (OOC). Ответь на него вне роли: коротко, по существу, '
            'без действий персонажа и звёздочек, от лица рассказчика. Потом можно одной фразой предложить вернуться к истории.')


def _shingles(text):
    words = _re.findall(r'\w+', text[:1500].lower())
    return frozenset(zip(words, words[1:], words[2:])) or frozenset(words)


def _similar(a, b, sa=None, sb=None):
    """Near-identical replies. A cheap word-trigram overlap filters out almost every pair first
    (measured: 140 replies, 1.36 s -> a few ms); difflib decides only for the look-alikes."""
    if abs(len(a) - len(b)) > max(len(a), len(b)) * 0.5:
        return False
    sa, sb = sa or _shingles(a), sb or _shingles(b)
    if sa and sb and len(sa & sb) / len(sa | sb) < 0.4:
        return False
    matcher = _difflib.SequenceMatcher(None, a[:1500], b[:1500])
    return matcher.quick_ratio() > _LOOP_SIMILARITY and matcher.ratio() > _LOOP_SIMILARITY


def repeated_replies(stored):
    """Assistant replies that occur at least twice (near-identical) among the newest ones."""
    replies = [r.get('content') for r in stored if r.get('role') == 'assistant' and r.get('status') == 'completed'
               and isinstance(r.get('content'), str)][-_LOOP_WINDOW:]
    marks = [_shingles(r) for r in replies]
    out = []
    for i, a in enumerate(replies):
        if any(_similar(a, r) for r in out):
            continue
        if any(_similar(a, b, marks[i], marks[j]) for j, b in enumerate(replies) if j != i):
            out.append(a)
    return out


def _quotes(fact, repeated):
    words = set(_re.findall(r'[а-яё]{4,}', fact.lower()))
    if not words:
        return False
    return any(len(words & set(_re.findall(r'[а-яё]{4,}', r.lower()))) / len(words) >= 0.6 for r in repeated)


def _memory_without(memory, repeated):
    if not memory or not repeated:
        return memory
    return '\n'.join(line for line in memory.split('\n') if not (line.startswith('- ') and _quotes(line[2:], repeated)))


def compile_messages(stored, *, character_name=None, character_description=None, budget=32768, context_window=None, response_reserve=512, preserve_recent_assistant=False, token_counter=None, memory=None):
    """Return OpenAI-style messages with strict role boundaries and bounded history.

    System role is composed from trusted configuration, not chat content.
    Reserve the newest user message; trim oldest completed history by characters.
    Token-level limits remain the llama-server's responsibility.
    """
    if not 1024 <= budget <= 1048576:
        raise ValueError('Invalid message budget')
    system = BASE_SYSTEM
    if character_name is not None or character_description is not None:
        name = (character_name or '').strip()
        description = (character_description or '').strip()
        if not name or not description or len(name)>120 or len(description)>12000:
            raise ValueError('Invalid character configuration')
        system = roleplay_system(name, description)
    repeated = [] if preserve_recent_assistant else repeated_replies(stored)
    memory = _memory_without(memory, repeated)
    if memory:
        # Stage 4h: this chat's memory (Core-built, budgeted) joins the trusted system text.
        system = system + '\n\n' + memory
    last_user = next((r.get('content') for r in reversed(stored) if r.get('role') == 'user'), '')
    if isinstance(last_user, str) and _OOC.match(last_user):
        system = system + OOC_NOTE
    # Conservative byte-based estimate, NOT an exact tokenizer. Reserve room for
    # output and template overhead; llama.cpp remains the exact token authority.
    if context_window is not None:
        if type(context_window) is not int or context_window < 512 or context_window > 131072:
            raise ValueError('INVALID_CONTEXT_WINDOW')
        if type(response_reserve) is not int or not 32 <= response_reserve < context_window:
            raise ValueError('INVALID_RESPONSE_RESERVE')
        if token_counter is not None:
            # Exact token counts from the loaded model's tokenizer (llama-server
            # /tokenize) plus per-message chat-template overhead.
            byte_budget = context_window - response_reserve - 64
            def cost(value): return token_counter(value) + 8
            overhead, units_per_byte = 0, 1/1.5
        else:
            byte_budget = (context_window - response_reserve - 96) * 2
            def cost(value): return len(value.encode('utf-8')) + 32
            overhead, units_per_byte = 16, 1
        if byte_budget <= 0:
            raise ValueError('CONTEXT_BUDGET_EXCEEDED')
    else:
        byte_budget = None
        def cost(value): return len(value)
        overhead, units_per_byte = 16, 1
    valid=[]
    for row in stored:
        role=row.get('role')
        if role not in ('user','assistant') or row.get('status') not in ('completed','persisted','queued','started','streaming'):
            continue
        content=row.get('content')
        if not isinstance(content,str) or not content.strip():
            continue
        # Do not feed partial assistant responses back to the model.
        if role=='assistant' and row.get('status')!='completed':
            continue
        valid.append({'role':role,'content':content})
    if repeated:
        merged=[]
        for message in valid:
            if message['role']=='assistant' and any(_similar(message['content'], r) for r in repeated):
                continue
            if message['role']=='user' and merged and merged[-1]['role']=='user':
                merged[-1]={'role':'user','content':merged[-1]['content']+'\n\n'+message['content']}
                continue
            merged.append(message)
        valid=merged
    if not valid or valid[-1]['role']!='user':
        raise ValueError('Missing latest user message')
    newest=valid[-1]
    from server.ego_core.languages import REPLY_ONLY, owner
    if owner() != 'ru':
        # 2026-11-06: next to the latest message the reply language wins over a Russian profile
        # and history (measured: without it a model answered English and German users in Russian).
        newest={'role':'user','content':newest['content']+'\n\n('+REPLY_ONLY[owner()]+')'}
        valid[-1]=newest
    used=cost(newest['content'])+cost(system)
    if byte_budget is not None and used > byte_budget:
        raise ValueError('CONTEXT_BUDGET_EXCEEDED')
    selected=[newest]
    if preserve_recent_assistant and (len(valid)<2 or valid[-2]['role']!='assistant'):
        raise ValueError('CONTEXT_BUDGET_EXCEEDED')
    for index,message in enumerate(reversed(valid[:-1])):
        size=cost(message['content'])+overhead
        capacity=(byte_budget if byte_budget is not None else budget)-used
        if preserve_recent_assistant and index==0 and size>capacity:
            # For continuation, carry the ending of the previous AI response,
            # not its full (potentially enormous) text. This only changes the
            # in-memory prompt; persisted chat content remains untouched.
            # UTF-8 byte accounting remains intentionally conservative.
            allowance=int((capacity-48)/units_per_byte)
            if allowance<128:
                raise ValueError('CONTEXT_BUDGET_EXCEEDED')
            tail=[]
            tail_bytes=0
            for ch in reversed(message['content']):
                width=len(ch.encode('utf-8'))
                if tail_bytes+width>allowance:
                    break
                tail.append(ch)
                tail_bytes+=width
            if not tail:
                raise ValueError('CONTEXT_BUDGET_EXCEEDED')
            message={'role':'assistant','content':''.join(reversed(tail))}
            size=cost(message['content'])+overhead
        if used+size>(byte_budget if byte_budget is not None else budget):
            if preserve_recent_assistant and index==0:
                raise ValueError('CONTEXT_BUDGET_EXCEEDED')
            break
        used+=size
        selected.append(message)
    dropped=len(valid)-len(selected)
    if dropped>0:
        # 2026-11-06: drop the oldest history in steps of TRIM_STEP messages, so the start of the
        # prompt moves once every few turns instead of every turn. llama-server can then reuse its
        # prompt cache (measured: an unchanged prefix costs ~0.03 s, a moved one ~2-8 s per reply).
        extra=-dropped%TRIM_STEP
        keep=max(2 if preserve_recent_assistant else 1, len(selected)-extra)
        selected=selected[:keep]
    return [{'role':'system','content':system}, *reversed(selected)]
