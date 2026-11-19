"""2026-11-06: a reply that started repeating does not reach the model again (real case «Новая история · Мара»)."""
from server.ego_core.roleplay_prompt import OOC_NOTE, compile_messages, repeated_replies

LOOP = ('*Ох, это странно...* *Я чувствую какое-то странное легкое в голове. Маяк вокруг меня начинает смотреться не так '
        'четко, а звуки звучат как издалека.* *Ох, что происходит?*')


def row(role, content):
    return {'role': role, 'content': content, 'status': 'completed'}


def history(*turns):
    return [row('user' if i % 2 == 0 else 'assistant', t) for i, t in enumerate(turns)]


def compile_(stored, memory=None):
    return compile_messages(stored, character_name='Мара', character_description='Картограф с маяка.', memory=memory)


def test_repeated_reply_is_left_out_and_user_turns_merge():
    stored = history('Привет', 'Здравствуй, путник. Чаю?', 'Газ усыпляет Мара', LOOP, 'Что с тобой?', LOOP,
                     'Мара, очнись!', LOOP + ' ', 'Ветер разгоняет газ')
    assert repeated_replies(stored) == [LOOP]
    messages = compile_(stored)
    text = '\n'.join(m['content'] for m in messages)
    assert 'звуки звучат как издалека' not in text
    roles = [m['role'] for m in messages[1:]]
    assert all(a != b for a, b in zip(roles, roles[1:])), roles              # strict alternation kept
    assert messages[-1]['content'].endswith('Ветер разгоняет газ') and 'Мара, очнись!' in messages[-1]['content']
    assert 'Здравствуй, путник' in text                                        # ordinary replies stay


def test_single_occurrence_and_continuation_are_untouched():
    stored = history('Привет', LOOP, 'Дальше')
    assert repeated_replies(stored) == []
    assert 'звуки звучат как издалека' in '\n'.join(m['content'] for m in compile_(stored))


def test_memory_facts_quoting_the_loop_are_left_out():
    stored = history('a', LOOP, 'b', LOOP, 'c')
    memory = 'Память этой истории:\n- Мара — картограф неизвестного\n- Маяк вокруг Мара начинает смотреться не так четко, звуки звучат как издалека'
    system = compile_(stored, memory)[0]['content']
    assert 'картограф неизвестного' in system and 'звуки звучат' not in system


def test_ooc_question_gets_an_out_of_role_note():
    for text in ('((Почему ты повторяешь?))', '[OOC: что происходит]', 'ООС: почему', 'OOC - вопрос'):
        assert compile_(history('Привет', 'Здравствуй.', text))[0]['content'].endswith(OOC_NOTE), text
    assert not compile_(history('Привет', 'Здравствуй.', 'Как дела?'))[0]['content'].endswith(OOC_NOTE)


def test_history_is_trimmed_in_blocks_for_the_prompt_cache():
    from server.ego_core import roleplay_prompt as sp
    stored = []
    for i in range(60):
        stored += [row('user', f'реплика {i} ' + 'слово ' * 40), row('assistant', f'ответ {i} ' + ' '.join(f'w{i}x{k}' for k in range(40)))]
    stored.append(row('user', 'последнее'))
    starts = set()
    for extra in range(0, 16, 2):                     # the story grows by a user/assistant pair per turn
        history = stored[:len(stored) - 16 + extra] + [row('user', 'последнее')]
        msgs = compile_messages(history, character_name='Мара', character_description='Картограф.',
                                     context_window=16384, response_reserve=1024)
        assert msgs[-1]['content'] == 'последнее'
        dropped = len(history) - (len(msgs) - 1)
        assert dropped % sp.TRIM_STEP == 0, dropped
        starts.add(msgs[1]['content'][:20])
    assert len(starts) <= 3                           # the start moved in steps, not every turn
