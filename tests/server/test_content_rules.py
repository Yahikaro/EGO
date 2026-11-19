"""The roleplay prompt carries one set of content rules, with hard limits."""
from server.ego_core import roleplay_prompt as rp

HISTORY = [{'role': 'user', 'content': 'Привет', 'status': 'completed'}]


def test_roleplay_system_uses_the_content_rules_with_hard_limits():
    text = rp.roleplay_system('Имя', 'Профиль.', language='ru')
    assert rp.CONTENT_RULES in text
    assert 'романтика и напряжение' in text
    assert 'несовершеннолетних' in text and 'реальных узнаваемых людей' in text and 'без согласия' in text


def test_compile_messages_has_the_same_rules():
    system = rp.compile_messages(HISTORY, character_name='Имя', character_description='Профиль.')[0]['content']
    assert rp.CONTENT_RULES in system
    assert 'романтика и напряжение' in system and 'Не отказывайся' not in system
