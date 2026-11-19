from server.ego_core.roleplay_prompt import compile_messages


def row(role, content):
    return {'role': role, 'status': 'completed', 'content': content}


def test_long_previous_answer_continues_using_tail_instead_of_failing():
    previous = 'Начало. ' + ('длинная история. ' * 450) + 'Завершение про артефакт номер девять.'
    chat = [row('user', 'Расскажи про десять артефактов'), row('assistant', previous),
            row('user', 'Продолжи непосредственно с места остановки.')]
    messages = compile_messages(chat, context_window=2048, response_reserve=512,
                                     preserve_recent_assistant=True)
    assert messages[-2]['role'] == 'assistant'
    assert 'Завершение про артефакт номер девять.' in messages[-2]['content']
    assert len(messages[-2]['content']) < len(previous)
    assert messages[-1]['role'] == 'user'


def test_short_previous_answer_unchanged():
    chat = [row('user', 'Привет'), row('assistant', 'Короткий ответ.'), row('user', 'Продолжи')]
    messages = compile_messages(chat, context_window=4096, response_reserve=512,
                                     preserve_recent_assistant=True)
    assert messages[-2]['content'] == 'Короткий ответ.'


def test_character_profile_too_large_has_explicit_budget_failure():
    chat = [row('user', 'Привет')]
    try:
        compile_messages(chat, character_name='Эвелин', character_description='описание' * 400,
                              context_window=2048, response_reserve=1024)
    except ValueError as exc:
        assert str(exc) == 'CONTEXT_BUDGET_EXCEEDED'
    else:
        raise AssertionError('Expected budget failure')
