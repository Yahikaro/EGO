from runtime.adapters.llama_cpp import StreamEvent
from server.ego_core.service import CoreService

def test_finish_reason_is_optional_and_preserved():
    assert StreamEvent('done',True,finish_reason='length').finish_reason=='length'
    assert StreamEvent('done',True).finish_reason is None

def test_no_raw_text_in_metrics():
    info=CoreService._extract_timings({'predicted_n':100,'prompt_n':220,'predicted_ms':3000},None)
    assert info['completion_tokens']==100
    assert info['prompt_tokens']==220
    assert info['tokens_per_second']>0
