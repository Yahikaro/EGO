from windows_ui.chat_format import render_markdown, render_conversation

def test_escape_html_and_script():
    text = render_markdown('<script>alert(1)</script>')
    assert '&lt;script&gt;' in text
    assert '<script>' not in text

def test_markdown_renders_emphasis():
    assert '<b>bold</b>' in render_markdown('**bold**')
    assert '<i>italic</i>' in render_markdown('*italic*')
    assert '<code>code</code>' in render_markdown('`code`')

def test_messages_escaped():
    value = render_conversation([{'role':'user', 'content':'<img src="https://example.com">'}, {'role':'assistant', 'content':'Hello'}])
    assert '<img' not in value
    assert '&lt;img' in value
    assert 'Hello' in value


def test_telegram_alignment_and_distinct_bubbles():
    markup = render_conversation([{'role':'user', 'content':'My message'},
                                  {'role':'assistant', 'content':'Bot answer'}])
    user_bubble = '<td width="78%" bgcolor="#245083"'
    ai_bubble = '<td width="78%" bgcolor="#18283f"'
    assert markup.index('<td width="22%">&nbsp;</td>') < markup.index(user_bubble)
    assert markup.index(ai_bubble) < markup.rindex('<td width="22%">&nbsp;</td>')
    assert 'My message' in markup and 'Bot answer' in markup
