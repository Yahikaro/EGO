from windows_ui.character_cards import profile_from_fields, profiles_from_json
import json

def test_profile_fields_are_bounded_and_roundtrip():
    result=profile_from_fields('Эвелин','Характер','Внешность','Манера речи','Завязка сцены')
    assert 'Характер' in result['description']
    assert 'Внешность' in result['description']
    assert 'Манера речи' in result['description']
    assert 'Завязка сцены' in result['description']
    assert profiles_from_json(json.dumps({'Эвелин':result},ensure_ascii=False))['Эвелин']==result

def test_profile_rejects_oversized_content():
    try: profile_from_fields('Эвелин','А'*4001,'','','')
    except ValueError: pass
    else: raise AssertionError('Too long character profile accepted')
