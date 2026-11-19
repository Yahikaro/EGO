"""Local text-to-speech (2026-11-08): Core side of EGO's TTS.

Heavy work runs in a separate worker process (server/ego_tts/worker.py, its own venv tts\\.venv)
that Core starts on demand on 127.0.0.1:18082; Core never imports torch. This module owns:
  * the voice library: built-in profiles (tts_voices.json) + user voices (encrypted store, table tts_voices);
  * voice identity vs. delivery: a voice is the permanent timbre; `emotion` only changes delivery
    parameters (speed / sampling) and, for user voices, may pick an emotion-specific reference clip;
  * text preparation: language detection, sentence-safe segmentation;
  * a single-thread job queue with de-duplication and an in-memory audio cache.
Nothing leaves the machine: the worker binds to localhost and needs no network after models are downloaded.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from collections import OrderedDict
from pathlib import Path

_ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
TTS_HOME = Path(os.environ.get('EGO_TTS_HOME') or (_ROOT / 'tts' if (_ROOT / 'tts').exists() or not (_ROOT.parent / 'tts').exists() else _ROOT.parent / 'tts'))
TTS_URL = os.environ.get('EGO_TTS_URL', 'http://127.0.0.1:18082').rstrip('/')
TTS_PYTHON = Path(os.environ.get('EGO_TTS_PYTHON', TTS_HOME / '.venv' / 'Scripts' / 'python.exe'))
WORKER = Path(__file__).resolve().parents[1] / 'ego_tts' / 'worker.py'
CATALOG = Path(__file__).with_name('tts_voices.json')
DEFAULT_ENGINE = 'xtts'
SEGMENT_LIMIT = {'ru': 180, 'default': 230}      # XTTS warns above ~182 chars for Russian
MAX_TEXT = 4000
LANGS = ('ru', 'en', 'fr', 'es', 'de')

# Delivery presets. They do not touch the voice (speaker latents stay the same); XTTS has no emotion
# input, so this is what it honestly supports: tempo + sampling looseness. See docs/TTS.md.
EMOTIONS = {
    'neutral':     {'speed': 1.00, 'temperature': 0.70},
    'calm':        {'speed': 0.94, 'temperature': 0.55},
    'joy':         {'speed': 1.06, 'temperature': 0.85},
    'irritation':  {'speed': 1.05, 'temperature': 0.80},
    'anger':       {'speed': 1.10, 'temperature': 0.90},
    'sadness':     {'speed': 0.88, 'temperature': 0.60},
    'surprise':    {'speed': 1.06, 'temperature': 0.90},
    'uncertainty': {'speed': 0.92, 'temperature': 0.80},
    'embarrassment': {'speed': 0.95, 'temperature': 0.78},
    'tiredness':   {'speed': 0.85, 'temperature': 0.55},
}
_EMOTION_WORDS = [   # (emotion, stems) — matched against action/narration text of a reply
    ('anger', ('злит', 'злоб', 'ярост', 'рявк', 'кричит', 'взбеш', 'angr', 'furious', 'shout', 'yell')),
    ('irritation', ('раздраж', 'фырк', 'закатыва', 'annoy', 'irritat', 'scoff')),
    ('joy', ('смеёт', 'смеет', 'улыба', 'радост', 'хихик', 'весел', 'laugh', 'smil', 'grin', 'giggl', 'happ')),
    ('sadness', ('груст', 'печал', 'плач', 'слёз', 'слез', 'всхлип', 'sad', 'cry', 'tear', 'sob')),
    ('surprise', ('удивл', 'изумл', 'ахает', 'ошеломл', 'surpris', 'gasp', 'astonish')),
    ('embarrassment', ('смущ', 'краснеет', 'покраснел', 'неловк', 'blush', 'embarrass', 'flustered')),
    ('uncertainty', ('неуверен', 'замялс', 'замялас', 'колебл', 'сомнева', 'hesitat', 'unsure', 'uncertain')),
    ('tiredness', ('устал', 'зева', 'сонн', 'измотан', 'tired', 'yawn', 'exhaust', 'sleepy')),
    ('calm', ('спокойн', 'тихо', 'мягко', 'calm', 'softly', 'gently')),
]


class TtsFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------- text preparation
def detect_language(text, fallback='ru'):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return fallback if fallback in LANGS else 'ru'
    cyr = sum('\u0400' <= c <= '\u04ff' for c in letters) / len(letters)
    if cyr > 0.3:
        return 'ru'
    return fallback if fallback in ('en', 'fr', 'es', 'de') else 'en'


def guess_emotion(text):
    low = (text or '').lower()
    best, hits = 'neutral', 0
    for emotion, stems in _EMOTION_WORDS:
        n = sum(low.count(s) for s in stems)
        if n > hits:
            best, hits = emotion, n
    return best


_SENT = re.compile(r'(?<=[.!?…»"])\s+|\n+')


def split_text(text, language='ru'):
    """Sentence-sized segments under the engine limit; never cuts inside a word."""
    limit = SEGMENT_LIMIT.get(language, SEGMENT_LIMIT['default'])
    text = re.sub(r'[ \t]+', ' ', text.replace('*', ' ')).strip()
    out, cur = [], ''
    for sent in (s.strip() for s in _SENT.split(text)):
        if not sent:
            continue
        pieces = [sent]
        if len(sent) > limit:      # long sentence: split at commas/dashes, then at spaces
            pieces, buf = [], ''
            for chunk in re.split(r'(?<=[,;:—–])\s+', sent):
                while len(chunk) > limit:
                    cut = chunk.rfind(' ', 0, limit)
                    cut = cut if cut > 0 else limit
                    pieces.append(chunk[:cut].strip())
                    chunk = chunk[cut:].strip()
                if buf and len(buf) + 1 + len(chunk) > limit:
                    pieces.append(buf)
                    buf = chunk
                else:
                    buf = (buf + ' ' + chunk).strip()
            if buf:
                pieces.append(buf)
        for p in pieces:
            if cur and len(cur) + 1 + len(p) > limit:
                out.append(cur)
                cur = p
            else:
                cur = (cur + ' ' + p).strip()
    if cur:
        out.append(cur)
    return [s for s in out if re.search(r'\w', s)]


# ---------------------------------------------------------------- voice library
def builtin_voices():
    try:
        return json.loads(CATALOG.read_text(encoding='utf-8'))['voices']
    except (OSError, ValueError, KeyError):
        return []


def demo_path(voice_id):
    if not re.fullmatch(r'[a-z0-9_-]{1,64}', voice_id or ''):
        return None
    p = TTS_HOME / 'demos' / f'{voice_id}.wav'
    return p if p.exists() else None


def check_sample(data):
    """User voice sample: 16-bit PCM WAV, mono/stereo, 6-30 s, >= 16 kHz, <= 6 MB."""
    if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= 6 * 1024 * 1024 or not data.startswith(b'RIFF'):
        raise TtsFailure('TTS_BAD_SAMPLE')
    try:
        with wave.open(io.BytesIO(data)) as w:
            rate, ch, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    except (wave.Error, EOFError):
        raise TtsFailure('TTS_BAD_SAMPLE') from None
    seconds = frames / rate if rate else 0
    if rate < 16000 or ch not in (1, 2) or width != 2 or not 6 <= seconds <= 30:
        raise TtsFailure('TTS_BAD_SAMPLE')
    return round(seconds, 1)


def _text_field(body, key, limit, required=False):
    v = body.get(key, '')
    if not isinstance(v, str) or len(v) > limit or (required and not v.strip()):
        raise TtsFailure('INVALID_INPUT')
    return v.strip()


def new_user_voice(body):
    """Validated metadata + sample bytes for store.put_tts_voice."""
    if not isinstance(body, dict) or body.get('consent') is not True:
        raise TtsFailure('INVALID_INPUT')       # the user confirms the voice is theirs or they have the right to use it
    try:
        data = base64.b64decode(body.get('wav_b64') or '', validate=True)
    except (ValueError, TypeError):
        raise TtsFailure('TTS_BAD_SAMPLE') from None
    seconds = check_sample(data)
    langs = [l for l in (body.get('languages') or []) if l in LANGS] or ['ru']
    category = body.get('category') if body.get('category') in ('female', 'male', 'other') else 'other'
    traits = [t for t in (body.get('traits') or []) if isinstance(t, str) and len(t) <= 24][:6]
    meta = {'name': _text_field(body, 'name', 60, True), 'description': _text_field(body, 'description', 300),
            'ref_text': _text_field(body, 'ref_text', 500), 'category': category, 'languages': langs, 'traits': traits,
            'engines': ['xtts', 'f5'] if body.get('ref_text') else ['xtts'], 'seconds': seconds,
            'source': 'user', 'license': 'Пользовательский образец; права подтверждает пользователь'}
    return meta, data


# ---------------------------------------------------------------- worker process
_worker_lock = threading.Lock()


def _http(method, path, body=None, timeout=10, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(TTS_URL + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            return (payload, dict(r.headers)) if raw else json.loads(payload or b'null')
    except urllib.error.HTTPError as e:
        try:
            code = json.loads(e.read() or b'{}').get('code', 'TTS_FAILED')
        except ValueError:
            code = 'TTS_FAILED'
        raise TtsFailure(code if code in ('TTS_BAD_SAMPLE', 'TTS_NO_MEMORY', 'TTS_LANGUAGE', 'TTS_MODEL_MISSING', 'TTS_VOICE_MISSING') else 'TTS_FAILED') from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise TtsFailure('TTS_UNAVAILABLE') from e


def health():
    try:
        return _http('GET', '/health', timeout=2)
    except TtsFailure:
        return None


def installed():
    return TTS_PYTHON.exists() and WORKER.exists() and (TTS_HOME / 'models' / 'xtts_v2' / 'model.pth').exists()


def ensure_worker(wait=40):
    if health():
        return
    with _worker_lock:
        if health():
            return
        if not installed():
            raise TtsFailure('TTS_UNAVAILABLE')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        log = open(TTS_HOME / 'worker.log', 'ab')
        subprocess.Popen([str(TTS_PYTHON), '-I', str(WORKER)], cwd=str(TTS_HOME), stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, creationflags=flags, env={**os.environ, 'EGO_TTS_HOME': str(TTS_HOME)})
        end = time.time() + wait
        while time.time() < end:
            time.sleep(0.5)
            if health():
                return
        raise TtsFailure('TTS_UNAVAILABLE')


def status():
    h = health()
    return {'installed': installed(), 'online': bool(h), 'worker': h, 'default_engine': DEFAULT_ENGINE,
            'engines': [{'id': 'xtts', 'name': 'XTTS-v2', 'languages': list(LANGS), 'cloning': True, 'license': 'CPML (некоммерческая)'},
                        {'id': 'f5', 'name': 'F5-TTS', 'languages': ['en', 'ru'], 'cloning': True, 'license': 'CC-BY-NC-4.0',
                         'note': 'только голоса с образцом и его текстом'}],
            'emotions': list(EMOTIONS)}


def unload():
    return _http('POST', '/unload', {}) if health() else {'ok': True}


# ---------------------------------------------------------------- jobs
class TtsJobs:
    """One worker thread (GPU is shared with the LLM and ComfyUI). Identical requests share one job;
    finished audio stays in an in-memory LRU cache (regenerable, never written to disk)."""
    CACHE_ITEMS, CACHE_BYTES = 64, 160 * 1024 * 1024

    def __init__(self, store, runner=None):
        self.store, self.runner = store, runner
        self.jobs, self.by_key, self.lock = {}, {}, threading.Lock()
        self.queue, self.wake = [], threading.Event()
        self.audio = OrderedDict()
        threading.Thread(target=self._loop, daemon=True, name='ego-tts').start()

    def resolve_voice(self, voice_id, emotion):
        for v in builtin_voices():
            if v['id'] == voice_id:
                return {'speaker': v['speaker']}, v
        try:
            meta, data = self.store.get_tts_voice(voice_id)
        except Exception:
            raise TtsFailure('TTS_VOICE_MISSING') from None
        refs = meta.get('emotion_refs') or {}
        if emotion in refs:              # optional emotion-specific clip of the same person
            try:
                _, data = self.store.get_tts_voice(refs[emotion])
            except Exception:
                pass
        return {'sample_b64': base64.b64encode(data).decode(), 'sample_sha': hashlib.sha256(data).hexdigest(),
                'ref_text': meta.get('ref_text') or None}, meta

    def request(self, body):
        if not isinstance(body, dict):
            raise TtsFailure('INVALID_INPUT')
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise TtsFailure('INVALID_INPUT')
        engine = body.get('engine') or DEFAULT_ENGINE
        if engine not in ('xtts', 'f5'):
            raise TtsFailure('INVALID_INPUT')
        lang = body.get('language') or 'auto'
        lang = detect_language(text, body.get('ui_language') or 'ru') if lang == 'auto' else lang
        if lang not in LANGS:
            raise TtsFailure('TTS_LANGUAGE')
        emotion = body.get('emotion') or 'neutral'
        if emotion == 'auto':
            emotion = guess_emotion(body.get('context') or text)
        if emotion not in EMOTIONS:
            raise TtsFailure('INVALID_INPUT')
        speed = body.get('speed', 1.0)
        if not isinstance(speed, (int, float)) or not 0.6 <= speed <= 1.6:
            raise TtsFailure('INVALID_INPUT')
        voice_id = body.get('voice')
        if not voice_id:        # no voice chosen: the first built-in one, else the user's first own voice
            try:
                first = (builtin_voices() or self.store.list_tts_voices() or [{'id': ''}])[0]
            except Exception:
                first = {'id': ''}
            voice_id = first['id']
        segments = split_text(text, lang)
        if not segments:
            raise TtsFailure('INVALID_INPUT')
        e = EMOTIONS[emotion]
        params = {'engine': engine, 'language': lang, 'segments': segments, 'emotion': emotion,
                  'speed': round(max(0.6, min(1.6, speed * e['speed'])), 3), 'temperature': e['temperature']}
        return voice_id, params

    def submit(self, body):
        voice_id, params = self.request(body)
        key = hashlib.sha256(json.dumps([voice_id, params], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            known = self.by_key.get(key)
            if known and known in self.jobs and self.jobs[known]['status'] != 'failed':
                if self.jobs[known]['status'] != 'done' or known in self.audio:
                    return self._view(known)
            job = {'id': uuid.uuid4().hex, 'key': key, 'voice': voice_id, 'params': params, 'status': 'queued',
                   'error': None, 'created': time.time(), 'started': None, 'finished': None, 'device': None, 'engine_seconds': None}
            self.jobs[job['id']], self.by_key[key] = job, job['id']
            self.queue.append(job['id'])
            for k in [k for k, j in self.jobs.items() if j['finished'] and time.time() - j['finished'] > 3600 and k not in self.audio]:
                self.by_key.pop(self.jobs.pop(k)['key'], None)
        self.wake.set()
        return self.view(job['id'])

    def _view(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            raise TtsFailure('NOT_FOUND')
        end = job['finished'] or time.time()
        p = job['params']
        return {'id': job['id'], 'status': job['status'], 'error': job['error'], 'voice': job['voice'],
                'language': p['language'], 'emotion': p['emotion'], 'segments': len(p['segments']),
                'position': self.queue.index(job_id) + 1 if job_id in self.queue else 0,
                'seconds': round(end - (job['started'] or end), 1), 'device': job['device']}

    def view(self, job_id):
        with self.lock:
            return self._view(job_id)

    def audio_of(self, job_id):
        with self.lock:
            if job_id not in self.audio:
                raise TtsFailure('NOT_FOUND')
            self.audio.move_to_end(job_id)
            return self.audio[job_id]

    def _remember(self, job_id, wav):
        self.audio[job_id] = wav
        while len(self.audio) > self.CACHE_ITEMS or sum(map(len, self.audio.values())) > self.CACHE_BYTES:
            self.audio.popitem(last=False)

    def _run(self, voice_id, params):
        voice, _ = self.resolve_voice(voice_id, params['emotion'])
        ensure_worker()
        wav, headers = _http('POST', '/synthesize', {**params, **voice}, timeout=900, raw=True)
        return wav, headers.get('X-Device')

    def _loop(self):
        while True:
            self.wake.wait()
            with self.lock:
                job_id = self.queue.pop(0) if self.queue else None
                if job_id is None:
                    self.wake.clear()
                    continue
                job = self.jobs[job_id]
                job['status'], job['started'] = 'running', time.time()
            try:
                wav, device = (self.runner or self._run)(job['voice'], job['params'])
                status, error = 'done', None
            except TtsFailure as e:
                wav, device, status, error = None, None, 'failed', e.code
            except Exception as e:                    # keep the worker thread alive
                print('tts job failed:', repr(e)[:300], file=sys.stderr, flush=True)
                wav, device, status, error = None, None, 'failed', 'TTS_FAILED'
            with self.lock:
                if wav:
                    self._remember(job_id, wav)
                job.update(status=status, error=error, finished=time.time(), device=device)
