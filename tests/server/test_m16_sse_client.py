import json
import pytest
from server.ego_core.control import parse_generation_sse


def test_sse_parser_handles_generation_and_keepalives():
    expected={'id':'a','state':'streaming','text':'hello'}
    lines=[': keepalive','','event: generation', 'data: '+json.dumps(expected), '', 'event: generation','data: '+json.dumps({'id':'a','state':'completed','text':'hello'}),'']
    rows=list(parse_generation_sse(lines))
    assert rows[0]==expected
    assert rows[-1]['state']=='completed'


def test_sse_parser_ignores_unknown_events_and_rejects_huge_payload():
    lines=['event: diagnostic','data: {"secret":"x"}','', 'event: generation', 'data: '+('x'*1048577),'']
    with pytest.raises(ValueError,match='INVALID_GENERATION_EVENT'):
        list(parse_generation_sse(lines))
