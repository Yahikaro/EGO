from server.ego_core.service import CoreService


def test_llama_completion_timings_are_numeric_and_private():
    payload = {'predicted_n': 128, 'prompt_n': 240, 'predicted_ms': 3200,
               'prompt': 'SECRET', 'predicted_per_second': 40.0}
    result = CoreService._extract_timings(payload, 128)
    assert result == {'tokens_per_second': 40.0, 'completion_tokens': 128,
                      'prompt_tokens': 240, 'generation_seconds': 3.2}
    assert 'SECRET' not in str(result)


def test_missing_timings_are_not_invented():
    result = CoreService._extract_timings({'prompt_n': 'wrong'}, None)
    assert result['tokens_per_second'] is None
    assert result['completion_tokens'] is None
