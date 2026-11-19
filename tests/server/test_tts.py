"""2026-11-08: local TTS — text preparation, emotion layer, sample checks, voice library and job flow (worker faked)."""
import base64
import io
import time
import wave

from server.ego_core import tts
from test_core_api import LocalClient, core, uid  # noqa: F401  (fixture reuse)

WAV = b'RIFF' + b'\x00' * 40


def make_wav(seconds, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b'\x00\x00' * int(seconds * rate))
    return buf.getvalue()


def test_split_never_cuts_words_and_respects_limit():
    text = ('Это очень длинное предложение, в котором много слов, запятых и пауз, ' * 6) + 'Конец! Ещё одно? Да.'
    parts = tts.split_text(text, 'ru')
    assert all(len(p) <= tts.SEGMENT_LIMIT['ru'] for p in parts) and len(parts) > 2
    words = text.replace('*', ' ').split()
    assert ' '.join(parts).split() == words                       # nothing lost, nothing glued or cut
    assert tts.split_text('*улыбается* — Привет.', 'ru') == ['улыбается — Привет.']
    assert tts.split_text('…  ', 'ru') == []


def test_language_and_emotion():
    assert tts.detect_language('Привет, как дела?') == 'ru'
    assert tts.detect_language('Hello there', 'ru') == 'en'
    assert tts.detect_language('Bonjour à tous', 'fr') == 'fr'
    assert tts.guess_emotion('Она смеётся и улыбается') == 'joy'
    assert tts.guess_emotion('He yells, furious') == 'anger'
    assert tts.guess_emotion('Просто текст') == 'neutral'
    assert set(tts.EMOTIONS) >= {'calm', 'joy', 'irritation', 'anger', 'sadness', 'surprise', 'uncertainty', 'embarrassment', 'tiredness'}


def test_sample_checks():
    assert tts.check_sample(make_wav(8)) == 8.0
    for bad in (WAV, make_wav(3), make_wav(40), make_wav(8, rate=8000), b'ID3' + b'\x00' * 100):
        try:
            tts.check_sample(bad)
        except tts.TtsFailure as e:
            assert e.code == 'TTS_BAD_SAMPLE'
        else:
            raise AssertionError(len(bad))


def test_voice_library_and_speak_job(core, monkeypatch):
    c = core[0]
    calls = []

    def fake_run(self, voice_id, params):
        calls.append((voice_id, params))
        time.sleep(0.05)
        return make_wav(1), 'cuda'
    monkeypatch.setattr(tts.TtsJobs, '_run', fake_run)

    voices = c.get('/v1/tts/voices').json()['voices']
    assert voices == []                                           # no voices are shipped: users add their own

    r = c.post('/v1/tts/voices', json={'name': 'Мой', 'wav_b64': base64.b64encode(make_wav(8)).decode(), 'consent': True})
    assert r.status_code == 201, r.text
    mine = r.json()['id']
    voices = c.get('/v1/tts/voices').json()['voices']
    assert [v['id'] for v in voices] == [mine] and not voices[0]['builtin']
    assert {'id', 'name', 'category', 'languages', 'license', 'source', 'engines'} <= set(voices[0])
    assert c.post('/v1/tts/voices', json={'name': 'x', 'wav_b64': base64.b64encode(make_wav(8)).decode()}).json()['code'] == 'INVALID_INPUT'
    assert c.post('/v1/tts/voices', json={'name': 'x', 'wav_b64': base64.b64encode(make_wav(2)).decode(), 'consent': True}).json()['code'] == 'TTS_BAD_SAMPLE'
    assert c.get(f'/v1/tts/voices/{mine}/demo').content.startswith(b'RIFF')

    body = {'text': '*злится* — Уходи. Сейчас же!', 'voice': voices[0]['id'], 'emotion': 'auto'}
    a = c.post('/v1/tts/speak', json=body).json()
    b = c.post('/v1/tts/speak', json=body).json()                 # duplicate -> same job, one generation
    assert a['id'] == b['id']
    for _ in range(100):
        a = c.get('/v1/tts/jobs/' + a['id']).json()
        if a['status'] in ('done', 'failed'):
            break
        time.sleep(0.02)
    assert a['status'] == 'done' and a['language'] == 'ru' and a['emotion'] == 'anger' and a['device'] == 'cuda'
    assert c.get(f"/v1/tts/jobs/{a['id']}/audio").content.startswith(b'RIFF')
    assert c.post('/v1/tts/speak', json=body).json()['id'] == a['id']   # cached audio reused
    assert len(calls) == 1 and calls[0][1]['speed'] > 1.0                # anger: faster delivery, same voice id
    assert calls[0][0] == voices[0]['id']

    assert c.request('DELETE', f'/v1/tts/voices/{mine}').json()['deleted'] is True
    assert c.request('DELETE', f'/v1/tts/voices/{mine}').status_code == 404
    assert c.post('/v1/tts/speak', json={'text': ' '}).json()['code'] == 'INVALID_INPUT'
