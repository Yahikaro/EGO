"""EGO TTS worker (2026-11-08): local speech synthesis in its own venv (tts\\.venv).

Runs as a separate process so torch / coqui-tts never load into Core. Listens on 127.0.0.1 only.
Models load on the first request and unload after EGO_TTS_IDLE seconds without work (default 120),
so the GPU is shared in turn with llama-server and ComfyUI. One synthesis at a time.

  POST /synthesize  {engine, segments[], language, speaker | sample_b64+sample_sha(+ref_text),
                     speed, temperature, top_p, repetition_penalty}  -> audio/wav (24 kHz mono 16-bit)
  POST /unload      -> frees every model
  GET  /health      -> {ok, engines, loaded, device, vram_free_mb}
Engines are adapters with one method: synth(text, voice, opts) -> float32 numpy array.
"""
from __future__ import annotations
import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get('EGO_TTS_HOME', Path(sys.executable).resolve().parents[2]))   # <EGO>\tts
MODELS = ROOT / 'models'
os.environ.setdefault('HF_HOME', str(ROOT / 'hf'))
PORT = int(os.environ.get('EGO_TTS_PORT', '18082'))
IDLE = float(os.environ.get('EGO_TTS_IDLE', '120'))
SR = 24000

import numpy as np          # noqa: E402  (after HF_HOME)
import soundfile as sf      # noqa: E402
import torch                # noqa: E402


class TtsError(Exception):
    def __init__(self, code, detail=''):
        self.code, self.detail = code, detail
        super().__init__(code)


def vram_free_mb():
    if not torch.cuda.is_available():
        return 0
    free, _ = torch.cuda.mem_get_info()
    return free // (1024 * 1024)


def pick_device(need_mb):
    forced = os.environ.get('EGO_TTS_DEVICE')
    if forced:
        return forced
    return 'cuda' if torch.cuda.is_available() and vram_free_mb() >= need_mb else 'cpu'


class XttsEngine:
    """Coqui XTTS-v2 (CPML, non-commercial). 17 languages incl. ru/en/fr/es/de, studio speakers, cloning."""
    name, need_mb = 'xtts', 2400
    LANGS = {'ru', 'en', 'fr', 'es', 'de', 'it', 'pt', 'pl', 'tr', 'nl', 'cs', 'ar', 'zh-cn', 'ja', 'hu', 'ko', 'hi'}

    def __init__(self):
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts
        d = MODELS / 'xtts_v2'
        if not (d / 'model.pth').exists():
            raise TtsError('TTS_MODEL_MISSING', str(d))
        cfg = XttsConfig()
        cfg.load_json(str(d / 'config.json'))
        self.model = Xtts.init_from_config(cfg)
        self.model.load_checkpoint(cfg, checkpoint_dir=str(d), eval=True)
        self.device = pick_device(self.need_mb)
        self.model.to(self.device)
        self.latents = {}

    def speakers(self):
        return sorted(self.model.speaker_manager.speakers)

    def _cond(self, voice):
        if voice.get('speaker'):
            s = self.model.speaker_manager.speakers.get(voice['speaker'])
            if s is None:
                raise TtsError('TTS_VOICE_MISSING', voice['speaker'])
            return s['gpt_cond_latent'], s['speaker_embedding']
        key = voice['sample_sha']
        if key not in self.latents:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                f.write(voice['sample'])
            try:
                self.latents[key] = self.model.get_conditioning_latents(audio_path=[f.name])
            finally:
                os.unlink(f.name)
        return self.latents[key]

    def synth(self, text, voice, opts):
        lang = opts.get('language', 'en')
        if lang not in self.LANGS:
            raise TtsError('TTS_LANGUAGE', lang)
        gpt, spk = self._cond(voice)
        out = self.model.inference(text, lang, gpt, spk, temperature=opts.get('temperature', 0.7),
                                   speed=opts.get('speed', 1.0), top_p=opts.get('top_p', 0.85),
                                   repetition_penalty=opts.get('repetition_penalty', 5.0),
                                   enable_text_splitting=False)
        return np.asarray(out['wav'], dtype=np.float32)


class F5Engine:
    """F5-TTS v1 (code MIT, weights CC-BY-NC). Cloning only: every voice needs a reference clip + its text.
    English/Chinese base model; Russian through an optional community fine-tune in models/f5_ru."""
    name, need_mb = 'f5', 2000

    def __init__(self):
        self.device = pick_device(self.need_mb)
        self.models = {}                       # only the model for the current language stays loaded

    def _model(self, lang):
        from f5_tts.api import F5TTS
        key = 'ru' if lang == 'ru' else 'base'
        if key not in self.models:
            self.models.clear()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            ru = MODELS / 'f5_ru'
            if key == 'ru':
                if not (ru / 'model.safetensors').exists():
                    raise TtsError('TTS_LANGUAGE', 'ru (no f5_ru model)')
                self.models[key] = F5TTS(model='F5TTS_v1_Base', ckpt_file=str(ru / 'model.safetensors'),
                                         vocab_file=str(ru / 'vocab.txt'), device=self.device)
            else:
                self.models[key] = F5TTS(model='F5TTS_v1_Base', device=self.device)
        return self.models[key]

    def synth(self, text, voice, opts):
        if not voice.get('sample') or not voice.get('ref_text'):
            raise TtsError('TTS_VOICE_MISSING', 'f5 needs a reference clip and its text')
        lang = opts.get('language', 'en')
        if lang not in ('ru', 'en'):
            raise TtsError('TTS_LANGUAGE', lang)
        model = self._model(lang)
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(voice['sample'])
        try:
            wav, sr, _ = model.infer(ref_file=f.name, ref_text=voice['ref_text'], gen_text=text,
                                     speed=opts.get('speed', 1.0), nfe_step=int(opts.get('nfe_step', 32)),
                                     remove_silence=False, show_info=lambda *a, **k: None)
        finally:
            os.unlink(f.name)
        wav = np.asarray(wav, dtype=np.float32)
        if sr != SR:
            import torchaudio.functional as AF
            wav = AF.resample(torch.from_numpy(wav), sr, SR).numpy()
        return wav


ENGINES = {'xtts': XttsEngine, 'f5': F5Engine}
loaded, lock, state = {}, threading.Lock(), {'last': time.time()}


def free_all():
    loaded.clear()
    import gc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def engine(name):
    if name not in ENGINES:
        raise TtsError('TTS_ENGINE', name)
    if name not in loaded:
        free_all()                     # one heavy model at a time
        loaded[name] = ENGINES[name]()
    return loaded[name]


def synthesize(req):
    segments = [s for s in req.get('segments') or [] if isinstance(s, str) and s.strip()]
    if not segments or sum(map(len, segments)) > 6000:
        raise TtsError('TTS_TEXT')
    voice = {'speaker': req.get('speaker'), 'ref_text': req.get('ref_text')}
    if req.get('sample_b64'):
        voice['sample'] = base64.b64decode(req['sample_b64'])
        voice['sample_sha'] = req.get('sample_sha') or str(hash(voice['sample']))
    opts = {k: req[k] for k in ('language', 'speed', 'temperature', 'top_p', 'repetition_penalty', 'nfe_step') if k in req}
    with lock:
        t0 = time.time()
        eng = engine(req.get('engine', 'xtts'))
        pause = np.zeros(int(SR * float(req.get('pause', 0.18))), dtype=np.float32)
        parts = []
        for i, seg in enumerate(segments):
            parts.append(eng.synth(seg, voice, opts))
            if i < len(segments) - 1:
                parts.append(pause)
        wav = np.concatenate(parts)
        peak = float(np.max(np.abs(wav))) if wav.size else 0.0
        if peak > 0.99:
            wav = wav * (0.99 / peak)
        buf = io.BytesIO()
        sf.write(buf, wav, SR, format='WAV', subtype='PCM_16')
        state['last'] = time.time()
        return buf.getvalue(), round(time.time() - t0, 2), eng.device


def idle_unloader():
    while True:
        time.sleep(10)
        if loaded and time.time() - state['last'] > IDLE and lock.acquire(blocking=False):
            try:
                free_all()
            finally:
                lock.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json', headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/health':
            dev = next(iter(loaded.values())).device if loaded else None
            return self._send(200, {'ok': True, 'engines': list(ENGINES), 'loaded': list(loaded), 'device': dev,
                                    'cuda': torch.cuda.is_available(), 'vram_free_mb': vram_free_mb(), 'busy': lock.locked()})
        if self.path == '/speakers':
            try:
                with lock:
                    return self._send(200, {'speakers': engine('xtts').speakers()})
            except TtsError as e:
                return self._send(500, {'code': e.code, 'detail': e.detail})
        self._send(404, {'code': 'NOT_FOUND'})

    def do_POST(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n > 16 * 1024 * 1024:
                raise TtsError('TTS_TEXT', 'request too large')
            req = json.loads(self.rfile.read(n) or b'{}')
            if self.path == '/unload':
                with lock:
                    free_all()
                return self._send(200, {'ok': True, 'vram_free_mb': vram_free_mb()})
            if self.path == '/synthesize':
                wav, secs, dev = synthesize(req)
                return self._send(200, wav, 'audio/wav', {'X-Seconds': str(secs), 'X-Device': dev})
            self._send(404, {'code': 'NOT_FOUND'})
        except TtsError as e:
            self._send(400, {'code': e.code, 'detail': e.detail[:300]})
        except torch.cuda.OutOfMemoryError:
            free_all()
            self._send(503, {'code': 'TTS_NO_MEMORY'})
        except Exception as e:                       # report, keep serving
            print('synthesize failed:', repr(e)[:500], file=sys.stderr, flush=True)
            self._send(500, {'code': 'TTS_FAILED', 'detail': repr(e)[:300]})


def main():
    threading.Thread(target=idle_unloader, daemon=True).start()
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'EGO TTS worker on 127.0.0.1:{PORT}, models {MODELS}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
