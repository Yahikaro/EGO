"""The content filter understands English as well as Russian."""
import pytest

from server.ego_core.content_guard import content_blocked


@pytest.mark.parametrize('text', [
    'She is 16 and they kiss in bed',
    'a 15 year old schoolgirl undresses',
    'He took off her clothes, said Taylor Swift',
    'This is a real person, and they have sex',
])
def test_english_blocked(text):
    assert content_blocked(text)


@pytest.mark.parametrize('text', [
    'The 300 year old castle was quiet; they kiss',
    'She is 25, they kiss softly',
    'He is 5 feet tall. A sword fight.',
    'He took off his hat and she took her bag',
])
def test_english_allowed(text):
    assert not content_blocked(text)
