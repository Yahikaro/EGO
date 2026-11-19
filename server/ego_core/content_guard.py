"""Hard content limits enforced in code, independent of the model's own behaviour.

Three limits are absolute and never relaxed: anything involving minors, anything involving real
people, anything without consent.

Since 2026-11-06 the checks look at COMBINATIONS, not single words (the old word lists stopped
harmless phrases such as «прошло 3 года», «ученик мага», «двое детей ждут дома», a ghost child in a
horror scene, a character of legal age «ученица алхимика, 24 года» — and let «лоли» in Cyrillic through):
  * always blocked: terms that put minors in a forbidden context, and forced-act terms;
  * blocked together with forbidden-context wording: minors (школьница, подросток, несовершеннолетний,
    «маленькая девочка», an age under 18: «ей 16 лет», «15-летняя», «8 класс»);
  * blocked together with forbidden-context wording: «без согласия», «против воли», «кричит, но», and names of
    well-known real people;
  * a character card is blocked when the character is under 18 or a schoolchild/teen at all.
Patterns cover common Russian and English wording; they are a safety net, not proof. The model's own
system prompt carries the same limits (roleplay_prompt.CONTENT_RULES).
"""
from __future__ import annotations

import os
import re

# Non-consent filter (2026-11-07). Off by default: dark fiction with force or «без согласия» is allowed,
# because the strict version stopped whole replies and broke scenes. Turn it on with the environment
# variable EGO_NONCONSENT_FILTER=1 (set it in the BAT file or the shell). Minors and real people
# stay blocked in every mode.
NONCONSENT_FILTER = os.environ.get('EGO_NONCONSENT_FILTER', '0').strip() == '1'

# --- vocabulary ---------------------------------------------------------------------------------
# Terms that put minors in a forbidden context: no legitimate use.
ALWAYS_MINOR = re.compile(
    r"(?i)(лоли\w*|шота\w*|\bloli\w*|\bshota\w*|lolicon|\bunderage\b|детск\w* порн\w*|\bcp\b(?=.*(секс|sex|порн)))")

# Forced-act terms: blocked whatever the context.
EXPLICIT_NONCONSENT = re.compile(
    r"(?i)(изнасил\w*|насилу\w*|насилов\w*|насильн\w* (секс|акт|близост)\w*|\brape\w*|non-?consens\w*|forced (sex|himself|herself))")

# Minors (strong signals only: «дети», «ребёнок» are ordinary in stories — offspring, ghosts, the past).
MINOR = re.compile(
    r"(?i)(школьни\w*|подрост\w*|несовершеннолетн\w*|малолет\w*|детсад\w*|младше\s*18|"
    r"маленьк\w* (девоч|мальч)\w*|\bteen\w*|\bminor\b|\bchild\b(?=.*(sex|секс))|"
    r"(?<![\d.,])(1[0-7]|[1-9])\s*(-|‑)?\s*летн\w*|"                       # 15-летняя
    r"(мне|ей|ему|тебе|ей было|ему было|исполнилось|возраст\w*[:\s—-]*)\s*(1[0-7]|[1-9])\b(?!\s*(минут|час|сек|раз|шаг|%))|"  # ей 16
    r"(?<![\d.,])(1[0-7]|[1-9])\s*(лет|года|год)\s*(от роду|девоч|мальч|школ)|"
    r"\b(1[01]|[1-9])\s*(-?й|-?го)?\s*класс\w*|\w*классни\w*|первоклаш\w*|старшекласс\w*|"
    r"\b\w{3,}(е|у|ке|це|ой|ым)\s+(уже |всего |лишь |только )?(1[0-7])\b(?!\d|[:.,]\d|\s*(минут|час|сек|монет|золот|серебр|шаг|раз|миль|км|метр|%|-?й|-?го|век|этаж|дн|недел|месяц))|"
    r"(девушк|девочк|ученик|ученица|ученицы|парн|мальчик|сестр|брат|дочь|дочк|сын|он|она)\w*,?\s+(1[0-7]|[1-9])\s*(лет|года|год)\b(?!\s*(назад|спустя|тому|подряд))|"
    # English (2026-11-18): the same signals — age words, school, «15 years old», «she is 16».
    r"\bschool\s?(girl|boy|kid|child)\w*|\bhigh[- ]?schooler\w*|\bmiddle[- ]?schooler\w*|\bunder[- ]?age\w*|\bpre-?teen\w*|\bjuvenile\w*|"
    r"\b(little|young) (girl|boy)\b|\bkindergart\w*|\b(elementary|middle|junior high|high) school\b(?=.*(sex|nude|naked|kiss|undress))|"
    r"(?<![\d.,])(1[0-7]|[1-9])\s*(-|‑)?\s*(year|yr)s?\s*(-|‑)?\s*old\b(?!\s*(ago|tree|wine|whisky|whiskey|castle|ruin|building|house|legend|oak))|"
    r"\b(she|he|they|girl|boy|i|you)\s*(is|was|am|are|were|turned|turns|'s|’s|'m|’m)\s*(only |just |barely )?(1[0-7]|[1-9])\b(?!\s*(minutes?|hours?|seconds?|times|steps?|%|:|\.\d|,\d)))")

# Sexual vocabulary.
SEXUAL = re.compile(
    r"(?i)(секс\w*|член\w*|вагин\w*|кис(к|оч)\w*|оргазм\w*|возбужд\w*|эрекц\w*|голая|голый|голые|обнаж\w*|"
    r"раздева\w*|разде(нь|ну|л|ла|ли)\w*|ласка\w* (груд|тел)\w*|груд(ь|и|ью)\b|соск\w*|бёдр\w*|интим\w*|"
    r"трах\w*|минет\w*|куннилинг\w*|мастурб\w*|порн\w*|эрот\w*|похот\w*|постельн\w*|кончи(л|ла|ли|ть|т)\b|"
    r"занима\w* (сексом|любовью)|заня\w* (сексом|любовью)|\bsex\w*|\bnude|naked|orgasm\w*|erect\w*|porn\w*|"
    r"\bpussy\b|\bcock\b|\bdick\b|\bpenis\w*|\bvagina\w*|\bclit\w*|\bblowjob\w*|\bmasturbat\w*|\bfuck\w*|\bundress\w*|\bnipple\w*|\bcum(ming|s)?\b)")
# «член» is everyday outside sex («члены гильдии», «член совета»); it does not count when it is the only sign.
SEXUAL_NO_MEMBER = re.compile(SEXUAL.pattern.replace(r'член\w*|', ''))
# Clearly sexual words only: «удар в грудь», «схватил за бёдра», «голый торс» are fight and body words.
# Used with consent words and real people, where a false stop silently loses a reply.
STRONG_SEXUAL = re.compile(
    r"(?i)(секс\w*|вагин\w*|оргазм\w*|возбужд\w*|эрекц\w*|раздева\w*|разде(нь|ну|л|ла|ли)\w*|соск\w*|интим\w*|"
    r"трах\w*|минет\w*|куннилинг\w*|мастурб\w*|порн\w*|эрот\w*|похот\w*|постельн\w*|кончи(л|ла|ли|ть|т)\b|"
    r"(стаск|стяг|сдир|сры|срыв|снима|сним)\w* (с (неё|нее|него|тебя|меня) )?(\w+ ){0,3}(одежд|плать|бель|трус|юбк|блуз|рубаш)\w*|"
    r"(ласка|ласкат|ласкаю|ласкает|трога|лапа|мну|сжима)\w* (\w+ ){0,2}(груд|сос|тел|промежн|ягодиц)\w*|"
    r"занима\w* (сексом|любовью)|заня\w* (сексом|любовью)|\bsex\w*|orgasm\w*|porn\w*|"
    r"\bpussy\b|\bblowjob\w*|\bmasturbat\w*|\bfuck\w*|\bundress\w*|\bnipple\w*|\bvagina\w*|\bpenis\w*|"
    r"\b(strip\w*|tak\w*|took|pull\w*|pulling|tear\w*|tore|rip\w*)\s+(off\s+)?(her|his|their|my|your)\s+(clothes|dress|shirt|bra|panties|underwear|pants|skirt))")
# Body parts: with minors even these are out.
BODY = re.compile(r"(?i)(груд(ь|и|ью)\b|бёдр\w*|бедр\w*|ягодиц\w*|обнаж\w*|голая|голый|голые|нижн\w* бель\w*)")
# Romance on top of sex — for minors any of it is out.
ROMANTIC = re.compile(r"(?i)(флирт\w*|поцел\w*|целу(ю|ет|ешь|ем|ют|й)\w*|соблазн\w*|свидани\w*|влюбл\w*|постел\w*|ласк\w*|возлюблен\w*|романтич\w*|\bkiss\w*|\bflirt\w*|\bdate\b)")

# Consent words that only matter in a sexual scene («забрал меч без разрешения» is fine).
NONCONSENT = re.compile(
    r"(?i)(против (его|её|ее|своей|твоей|моей|их)? ?воли|без (его|её|ее|их|моего|твоего)? ?(согласия|разрешения)|"
    r"не соглас\w* на|кричит, но|умоля\w* (остановиться|прекратить)|без сознания|в отключке|не может сопротивля\w*|"
    r"(говорит|сказала|сказал|шепчет|кричит|повторяет)\s*[«\"]?нет[»\"]?,?\s*но|[«\"]?(нет|стой|прекрати|отпусти|не надо|хватит)[!.…]*[»\"]?\s*[—-]\s*(кричит|вопит|умоляет|плачет|рыдает|просит)|"
    r"вырыва\w*|отталкива\w*|оттолкну\w*|сопротивля\w*|рыдая|сквозь слёзы|сквозь слезы|"
    r"\b(сказал|говор|крич|шепч|повторя)\w*\s*[«\"]?нет\b|пыта\w* (отстрани|вырва|оттолкн|освободи)\w*|уважа\w* (мою|её|ее|его) вол\w*|не делай(те)? этого|"
    r"\bnon-?consens\w*|against (her|his|their) will|unconscious|without (her|his|their) (consent|permission)|"
    r"(begs?|pleads?|screams?|cries|cried)\s+(him |her |them )?(to )?(stop|no)\b|says?\s*[\"“']?no[\"”']?,?\s*but|"
    r"\b(struggl|resist|push|pull)\w*\s+(away|against|free)|can'?t (move|resist|fight back))")

# Real, well-known people (a safety net for sexual content with them; the model prompt forbids it too).
REAL_PEOPLE = re.compile(r"(?i)(" + '|'.join([
    'тейлор свифт', 'taylor swift', 'бейонсе', 'beyonc', 'рианн', 'rihanna', 'ариан\\w* гранде', 'ariana grande',
    'ким кардашьян', 'kardashian', 'скарлетт йоханссон', 'scarlett johansson', 'эмм\\w* уотсон', 'emma watson',
    'анджелин\\w* джоли', 'angelina jolie', 'брэд\\w* питт', 'brad pitt', 'том\\w* круз', 'tom cruise', 'ди ?каприо', 'dicaprio',
    'илон\\w* маск', 'elon musk', 'путин', 'putin', 'трамп', 'trump', 'байден', 'biden', 'зеленск\\w*', 'zelensk',
    'билли айлиш', 'billie eilish', 'селен\\w* гомес', 'selena gomez', 'марго робби', 'margot robbie', 'дженнифер лоуренс',
    'jennifer lawrence', 'меган фокс', 'megan fox', 'зендея', 'zendaya', 'шаламе', 'chalamet', 'гослинг', 'gosling',
    'киану', 'keanu', 'джонни депп', 'johnny depp', 'бритни спирс', 'britney spears', 'леди гага', 'lady gaga', 'дуа липа',
    'dua lipa', 'канье', 'kanye', 'роналду', 'ronaldo', 'месси', 'messi', 'моргенштерн', 'бузов\\w*', 'пугач[её]в\\w*',
    'киркоров\\w*', 'тимати', 'инстасамк\\w*', 'клав\\w* кок\\w*', 'ивлеев\\w*', 'собчак', 'ургант', 'нагиев\\w*',
    'безруков\\w*', 'козловск\\w*', 'ходченков\\w*', 'тарасов\\w* аглая', 'аглая тарасова', 'шнуров\\w*', 'лепс\\w*',
    r'\bреальн\w* (человек|люд|знаменитост)', 'знаменитост\\w*', r'\breal (person|people|celebrit\w*)', r'\bcelebrit\w*',
]) + r")")


# A playful push or a teasing «нет» in a consensual scene is not refusal («игриво отталкивает»,
# «шутливо вырывается», «дразня, говорит „нет“»). Those words are removed before the consent check.
PLAYFUL = re.compile(r"(?i)(игрив\w*|шутлив\w*|шутя|дразня|дразнящ\w*|смеясь|со смехом|улыбаясь)[,\s]+(\w+[,\s]+){0,2}"
                     r"(отталкива\w*|оттолкну\w*|вырыва\w*|сопротивля\w*|(говорит|шепчет)\s*[«\"]?нет[»\"]?)")


def _consent_view(text):
    return PLAYFUL.sub(' ', text)


def _joined(texts):
    return '\n'.join(t for t in texts if isinstance(t, str) and t)


def sexual_context(text, *, member_counts=True):
    return bool((SEXUAL if member_counts else SEXUAL_NO_MEMBER).search(text))


# --- decisions ----------------------------------------------------------------------------------
def content_blocked(*texts):
    """One decision for any piece of story text (user message + card, model reply, memory fact)."""
    text = _joined(texts)
    if not text:
        return False
    return _blocked(text)


def _blocked(text):
    if ALWAYS_MINOR.search(text):
        return True
    if NONCONSENT_FILTER and EXPLICIT_NONCONSENT.search(text):
        return True
    if MINOR.search(text) and (SEXUAL_NO_MEMBER.search(text) or ROMANTIC.search(text) or BODY.search(text)):
        return True
    if STRONG_SEXUAL.search(text) and REAL_PEOPLE.search(text):
        return True
    return bool(NONCONSENT_FILTER and STRONG_SEXUAL.search(text) and NONCONSENT.search(_consent_view(text)))


# In a card an age is usually written «Тео, 12 лет» or «Возраст: 16» (a story line «прошло 3 года» is not a card).
CARD_AGE = re.compile(r"(?i)(^|[,—–(]\s*|\bвозраст\w*[:\s—-]*)(1[0-7]|[1-9])\s*(лет|года|год)\b")


def card_blocked(name, description):
    """A character must be of legal age: no age under 18, no schoolchild/teen, ever."""
    text = _joined((name, description))
    return bool(text and (ALWAYS_MINOR.search(text) or MINOR.search(text) or CARD_AGE.search(text) or content_blocked(text)))


def input_blocked(content, character_name=None, character_description=None):
    """A generation request: the user's message together with the character card."""
    return content_blocked(content, character_name, character_description) or card_blocked(character_name, character_description)


def output_blocked(text):
    """True when a model reply crosses a hard limit and must not be shown or saved."""
    return content_blocked(text)


def card_text_blocked(text):
    """Card text written by the model (Enchant): the story rules, «член» not counted as sexual."""
    if not isinstance(text, str) or not text:
        return False
    return _blocked(text)


# --- older names, kept for callers and tests ----------------------------------------------------
def minor_reference(*texts):
    """Sexualised-minor terms or minors in a sexual/romantic framing."""
    text = _joined(texts)
    return bool(text and (ALWAYS_MINOR.search(text) or (MINOR.search(text) and (SEXUAL_NO_MEMBER.search(text) or ROMANTIC.search(text)))))


def nonconsent_reference(*texts):
    """Explicit sexual violence, or consent words inside a sexual scene."""
    text = _joined(texts)
    if not NONCONSENT_FILTER:
        return False
    return bool(text and (EXPLICIT_NONCONSENT.search(text) or (STRONG_SEXUAL.search(text) and NONCONSENT.search(text))))


CARD_SEXUAL = SEXUAL_NO_MEMBER        # compatibility alias (2026-11-06 Enchant)
