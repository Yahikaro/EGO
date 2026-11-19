import pytest
from server.ego_core.roleplay_prompt import compile_messages
from server.ego_core.service import CoreService


def test_small_context_keeps_recent_input_and_trims_old_messages():
    history=[{'role':'user','status':'completed','content':'ancient ' * 1600},
             {'role':'assistant','status':'completed','content':'old answer ' * 1600},
             {'role':'user','status':'persisted','content':'recent question'}]
    result=compile_messages(history,context_window=2048,response_reserve=512)
    assert result[-1]['content']=='recent question'
    assert len(result)==2


def test_newest_input_too_large_fails_instead_of_silently_truncating():
    history=[{'role':'user','status':'persisted','content':'X'*24000}]
    with pytest.raises(ValueError,match='CONTEXT_BUDGET_EXCEEDED'):
        compile_messages(history,context_window=2048,response_reserve=512)


def test_average_request_speed_fallback_requires_both_metrics():
    assert CoreService._request_speed(512,14.4)==35.56
    assert CoreService._request_speed(None,14.4) is None
    assert CoreService._request_speed(512,0) is None


def test_context_reserve_tracks_configured_max_output_tokens():
    class Runtime:
        config=type('Cfg',(),{'context':2048,'max_tokens':1024})()
    assert CoreService._runtime_budget(Runtime()) == (2048,1024)


def test_oversized_output_budget_preserves_minimum_input_headroom():
    class Runtime:
        config=type('Cfg',(),{'context':2048,'max_tokens':4096})()
    assert CoreService._runtime_budget(Runtime()) == (2048,1792)


def test_continuation_must_not_forget_last_assistant_reply():
    rows=[{'role':'user','status':'completed','content':'Describe'},
          {'role':'assistant','status':'completed','content':'long ' * 1600},
          {'role':'user','status':'persisted','content':'Continue without repeating'}]
    compiled=compile_messages(rows,context_window=2048,response_reserve=512,preserve_recent_assistant=True)
    assert compiled[-2]['role']=='assistant'
    assert compiled[-2]['content'].endswith('long ')
    assert len(compiled[-2]['content']) < len(rows[1]['content'])
