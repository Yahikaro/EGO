"""Turn a stored character (and its world) into the roleplay profile for the model.

Decision D2: characters and worlds live in the encrypted Core store. A generation
may name `character_id`; Core composes the profile itself instead of trusting a
per-request description. Field names follow the EGO client's character studio.
"""
from __future__ import annotations

MAX_PROFILE_CHARS = 12000

_CHARACTER_FIELDS = (
    ('age', 'Возраст'), ('role', 'Роль'), ('appearance', 'Внешность'), ('personality', 'Характер'),
    ('behavior', 'Поведение и манера речи'), ('description', 'Описание'),
)
_WORLD_FIELDS = (('genre', 'Жанр'), ('subtitle', 'Кратко'), ('description', 'Описание мира'),
                 ('lore', 'Лор'), ('canon', 'Канон'))


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ''


def find_world(character, worlds):
    """The client links a character to a world by its name (or id)."""
    wanted = _text(character.get('world'), 120).casefold()
    if not wanted:
        return None
    for world in worlds:
        data = world.get('data', world)
        if wanted in (_text(data.get('name'), 120).casefold(), str(world.get('id', '')).casefold()):
            return data
    return None


def compose_profile(character, world=None):
    """Return (name, description) for compile_messages."""
    name = _text(character.get('name'), 120)
    if not name:
        raise ValueError('Invalid character configuration')
    lines = [f'Имя: {name}']
    for key, label in _CHARACTER_FIELDS:
        value = _text(character.get(key), 5000)
        if value:
            lines.append(f'{label}: {value}')
    tags = character.get('tags')
    if isinstance(tags, list):
        clean = [t.strip() for t in tags if isinstance(t, str) and t.strip()][:8]
        if clean:
            lines.append('Теги: ' + ', '.join(clean))
    place = [_text(character.get('world'), 120), _text(character.get('setting'), 120)]
    if any(place):
        lines.append('Мир и место: ' + ' · '.join(p for p in place if p))
    if world:
        for key, label in _WORLD_FIELDS:
            value = _text(world.get(key), 2500)
            if value:
                lines.append(f'{label}: {value}')
    scenario = _text(character.get('scenario'), 5000)
    if scenario:
        lines.append(f'Сценарий: {scenario}')
    greeting = _text(character.get('greeting'), 2000)
    if greeting:
        lines.append(f'Первая реплика {name} в сцене: {greeting}')
    return name, '\n'.join(lines)[:MAX_PROFILE_CHARS]
