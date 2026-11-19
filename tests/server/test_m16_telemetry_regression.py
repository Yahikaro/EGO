from runtime.adapters.llama_cpp import LlamaRuntime


def test_usage_after_finish_is_not_lost():
    data = [
        {'choices':[{'delta':{'content':'Привет'},'finish_reason':None}]},
        {'choices':[{'delta':{},'finish_reason':'stop'}]},
        {'choices':[], 'usage':{'prompt_tokens':42, 'completion_tokens':9}},
    ]
    state = {'finish_reason':None, 'prompt_tokens':None,'completion_tokens':None}
    for item in data:
        LlamaRuntime._consume_chat_metrics(item, state)
    assert state == {'finish_reason':'stop','prompt_tokens':42,'completion_tokens':9}


def test_usage_rejects_invalid_values():
    state={'finish_reason':None,'prompt_tokens':None,'completion_tokens':None}
    LlamaRuntime._consume_chat_metrics({'choices':[], 'usage':{'prompt_tokens':'secret','completion_tokens':-1}},state)
    assert state['prompt_tokens'] is None and state['completion_tokens'] is None
