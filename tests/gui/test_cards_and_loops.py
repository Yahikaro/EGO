from windows_ui.character_cards import add_card,cards_from_json
import json

def test_card_roundtrip():
    card=add_card({},'Эвелин','Готическая вампирша')
    assert cards_from_json(json.dumps(card,ensure_ascii=False)) == card

def test_bad_card_rejected():
    try: add_card({},'','abc')
    except ValueError: pass
    else: raise AssertionError('invalid card accepted')
