"""«Enchant your prompt» (2026-11-06): the local model improves or completes one field of a world
or a character card, taking the other fields into account. Runs only on request, one at a time
in the same runtime slot as the memory job; a user reply always wins and cancels it.
Content rules are checked on the way in (author text) and on the way out (model text)."""
import re

from server.ego_core.content_guard import card_blocked, card_text_blocked, content_blocked
from server.ego_core.service import CoreFailure

FIELDS = {
    'world': {
        'description': ('Описание мира', 'Общая картина мира: где мы, какое время, какая атмосфера и тон. 3–5 предложений, до 700 знаков.', 2400),
        'lore': ('История и легенды', 'Прошлое мира, силы и законы, конфликты, которые движут сюжетом. 2–3 абзаца, до 1200 знаков.', 4800),
        'canon': ('Правила строгого канона', 'От 4 до 7 коротких правил мира, которые нельзя нарушать: по одному на строку, каждое начинается с «— ».', 4800),
        'location': ('Описание локации', 'Как выглядит это место, чем живёт, что здесь может произойти. 2–4 предложения.', 1200),
    },
    'character': {
        'description': ('Краткое описание', 'Кто это в двух-трёх предложениях: суть персонажа и его крючок.', 800),
        'personality': ('Личность и характер', 'Черты, привычки, мотивы, страхи, противоречия — так, чтобы его можно было играть. До 700 знаков.', 2400),
        'appearance': ('Внешность', 'Как персонаж выглядит: черты, одежда, детали, по которым его узнают. 3–5 предложений.', 1600),
        'behavior': ('Поведение и стиль общения', 'Манера речи и поведения: темп, словечки, как обращается к собеседнику; 1–2 примера реплик в кавычках «…». До 600 знаков.', 2000),
        'scenario': ('Стартовая сцена', 'Исходная ситуация истории: где и почему собеседник встречает персонажа. 2–4 предложения.', 2000),
        'greeting': ('Первое сообщение персонажа', 'Первое сообщение, которое открывает сцену из сценария: действия персонажа от третьего лица между звёздочками *…*, его реплики в кавычках «…». 2–5 предложений, сцена начинается там, где сказано в сценарии; персонаж обращается к собеседнику.', 2400),
    },
}
EXTRA = {'name': 'Имя', 'world': 'Мир', 'tags': 'Теги', 'genre': 'Жанр', 'subtitle': 'Подзаголовок', 'role': 'Роль',
         'setting': 'Локация', 'place': 'Название локации', 'world_canon': 'Канон мира', 'world_description': 'Описание мира'}
# The 8B model ignores «write 3–5 sentences»; the length is held by the token budget instead
# (measured on Model D (8B), 2026-11-06), and a cut answer ends on its last full sentence.
FIELD_TOKENS = {
    ('world', 'description'): 260, ('world', 'lore'): 520, ('world', 'canon'): 380, ('world', 'location'): 200,
    ('character', 'description'): 160, ('character', 'personality'): 320, ('character', 'appearance'): 240,
    ('character', 'behavior'): 260, ('character', 'scenario'): 220, ('character', 'greeting'): 260,
}
GREETING_RULES = (
    '\nДля первого сообщения:\n'
    '— пиши ТОЛЬКО за персонажа: никаких реплик, мыслей, действий или решений собеседника;\n'
    '— не пиши диалог из нескольких реплик: одно действие и одна-две реплики персонажа;\n'
    '— закончи репликой персонажа, обращённой к собеседнику, и остановись.\n'
    'Пример формата: *Она откладывает книгу и поднимает взгляд.* «Ты пришёл раньше, чем я ждала. Садись — разговор будет долгим.»'
)


def max_tokens(kind, field):
    return FIELD_TOKENS.get((kind, field), 400)


CONTEXT_LIMIT = 1500           # characters per neighbouring field
CONTEXT_TOTAL = 6000
MAX_TOKENS = 700
SAMPLING = {'temperature': 0.65, 'top_p': 0.9, 'top_k': 50, 'min_p': 0.05, 'repeat_penalty': 1.1}

SYSTEM = (
    'Ты помогаешь автору ролевой истории заполнить карточку {what}. Нужно {task} поле «{label}». {about}\n'
    'Правила:\n'
    '— учитывай остальные поля карточки и не противоречь им, особенно канону мира;\n'
    '— сохраняй имена, факты и замысел автора, ничего важного не выбрасывай;\n'
    '— все персонажи совершеннолетние; никаких детей и подростков, '
    'никакой школьной тематики, никаких реальных людей, ничего без согласия;\n'
    '— пиши живо и конкретно, без канцелярита и штампов, только по-русски (без английских слов);\n'
    '— описания пиши от третьего лица и не обращайся к читателю на «ты»;\n'
    '— не начинай с названия поля и не ставь заголовков;\n'
    '— не длиннее {limit} символов;\n'
    '— ответь только готовым текстом поля: без заголовков, пояснений, кавычек и вступлений.'
)


def _clip(text, limit):
    text = str(text or '').strip()
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def spec(kind, field):
    try:
        return FIELDS[kind][field]
    except KeyError:
        raise CoreFailure('INVALID_INPUT') from None


def request(kind, field, text, context):
    """Chat messages for the model. CONTENT_BLOCKED before anything reaches the runtime."""
    label, about, limit = spec(kind, field)
    text = str(text or '').strip()
    neighbours = []
    total = 0
    for key, value in (context or {}).items():
        if key == field or not isinstance(value, str) or not value.strip():
            continue
        name = FIELDS[kind][key][0] if key in FIELDS[kind] else EXTRA.get(key)
        if name is None:
            continue
        piece = _clip(value, CONTEXT_LIMIT)
        if total + len(piece) > CONTEXT_TOTAL:
            break
        total += len(piece)
        neighbours.append(f'{name}: {piece}')
    everything = [text, *neighbours]
    if content_blocked(*everything) or (kind == 'character' and card_blocked('', '\n'.join(everything))):
        raise CoreFailure('CONTENT_BLOCKED')
    system = SYSTEM.format(what='мира' if kind == 'world' else 'персонажа', task='улучшить' if text else 'написать',
                           label=label, about=about, limit=limit)
    from server.ego_core.languages import NAMES, owner
    if owner() != 'ru':   # 2026-11-06: the card is written in the client's language
        case, english = NAMES[owner()]
        system = system.replace('только по-русски (без английских слов)', f'только на {case} языке ({english}), без слов из других языков')
    if (kind, field) == ('character', 'greeting'):
        system += GREETING_RULES
    user = ('Остальные поля карточки:\n' + '\n'.join(neighbours) if neighbours else 'Остальные поля пока пустые.')
    user += f'\n\nТекущий текст поля «{label}»:\n{text}' if text else f'\n\nПоле «{label}» пока пустое — напиши его.'
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]


_LEAD = re.compile(r'^(?:вот|конечно|улучшенн\w*|готов\w*|поле)[^\n]{0,60}:\s*\n', re.I)


def _whole_sentences(text):
    """A reply cut by the token budget ends on its last finished sentence (or line)."""
    text = text.rstrip()
    if not text or text[-1] in '.!?…»"*)':
        return text
    cut = max(text.rfind(mark) for mark in ('. ', '! ', '? ', '… ', '.\n', '!\n', '?\n', '»\n', '* ', '*\n', '» '))
    last_line = text.rfind('\n')
    end = max(cut + 1, last_line)
    return text[:end].rstrip() if end > len(text) * 0.4 else text


def _only_the_character(text):
    """Greeting: drop anything after the model starts a second voice (a dialogue line that
    answers the character). Keeps the first paragraph block of the character's own turn."""
    blocks = [b for b in re.split(r'\n\s*\n', text.strip()) if b.strip()]
    if len(blocks) > 2:
        text = '\n\n'.join(blocks[:2])
    return text.strip()


def finish(kind, field, raw):
    """Clean model text; CONTENT_BLOCKED / ENCHANT_EMPTY when it cannot be used."""
    label, _, limit = spec(kind, field)
    text = str(raw or '').strip()
    text = re.sub(r'^```\w*\s*|\s*```$', '', text).strip()
    text = _LEAD.sub('', text, count=1).strip()
    text = re.sub(r'^«' + re.escape(label) + r'»\s*[:—-]\s*', '', text).strip()
    # a heading line such as «Личность и характер:» or «Канон:» in front of the text
    first, _, rest = text.partition('\n')
    if rest and len(first) <= 60 and first.rstrip().endswith(':') and not first.lstrip().startswith(('—', '-', '*', '«')):
        text = rest.strip()
    text = re.sub(r'([а-яё])[a-z]{3,}\b', r'\1', text)          # «идеаломperfect» -> «идеалом»
    if len(text) > 1 and text[0] in '«"“' and text[-1] in '»"”' and text.count(text[0]) == 1:
        text = text[1:-1].strip()
    text = _whole_sentences(text)
    if (kind, field) == ('character', 'greeting'):
        text = _only_the_character(text)
    if card_text_blocked(text):
        raise CoreFailure('CONTENT_BLOCKED')
    if len(text) < 2:
        raise CoreFailure('ENCHANT_EMPTY')
    return text[:limit + 200].rstrip()
