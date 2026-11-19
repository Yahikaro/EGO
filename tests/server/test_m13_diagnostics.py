from tools.windows.runner import core_diagnostic_fields
from windows_ui.diagnostics import _projection


def test_runtime_diagnostics_keep_measured_metadata_and_drop_prompts():
    payload={'runtime_state':'READY','runtime_pid':1234,'model_ready':True,
        'model_load_seconds':1.5,'generation_metrics':{'tokens_per_second':40.,'prompt_tokens':30,'text':'DO_NOT_EXPORT'},
        'last_runtime_error':{'code':'RUNTIME_EXITED','exit_code':3221225781,'prompt':'DO_NOT_EXPORT'},
        'prompt':'DO_NOT_EXPORT'}
    result=core_diagnostic_fields(payload)
    assert result['runtime_state']=='READY' and result['runtime_pid']==1234
    assert result['model_ready'] is True
    assert result['last_runtime_error']['exit_code']==3221225781
    assert result['generation_metrics']['tokens_per_second']==40
    assert 'DO_NOT_EXPORT' not in str(result)
    projected=_projection(payload)
    assert projected['runtime_state']=='READY' and 'DO_NOT_EXPORT' not in str(projected)
