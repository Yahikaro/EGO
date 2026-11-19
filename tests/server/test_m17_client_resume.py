"""SSE parser and reconnect tests independent of Windows and SQLCipher."""
from server.ego_core.control import parse_generation_sse


def test_parser_can_return_cursor_without_changing_default_consumers():
    lines=['id: '+('a'*64),'event: generation','data: {"id":"g","state":"streaming","text":"hello"}','']
    assert list(parse_generation_sse(lines))[0]['text']=='hello'
    events=list(parse_generation_sse(lines, with_cursor=True))
    assert events == [(('a'*64),{'id':'g','state':'streaming','text':'hello'})]


def test_parser_rejects_malformed_cursor():
    lines=['id: forged','event: generation','data: {"id":"g","state":"streaming"}','']
    import pytest
    with pytest.raises(ValueError, match='INVALID_EVENT_CURSOR'):
        list(parse_generation_sse(lines, with_cursor=True))
