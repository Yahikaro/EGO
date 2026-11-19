"""Local image generation through ComfyUI (2026-11-08).

Core builds an SDXL txt2img workflow (checkpoint -> optional LoRA chain -> KSampler -> VAE decode
-> PreviewImage), queues it on the local ComfyUI API, waits for the result and stores the picture
in the encrypted Core store (media_images). ComfyUI keeps only a temp copy (PreviewImage).
Stdlib only: urllib, no new dependencies. Minors are blocked: prompts pass content_guard and an
underage negative is always appended.
"""
from __future__ import annotations
import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from server.ego_core.content_guard import content_blocked, minor_reference

COMFY_URL = os.environ.get('EGO_COMFYUI_URL', 'http://127.0.0.1:8188').rstrip('/')
MINOR_NEGATIVE = 'child, children, kid, teen, teenager, underage, minor, loli, schoolgirl, young girl, petite child body'
SAMPLERS = {'euler', 'euler_ancestral', 'dpmpp_2m', 'dpmpp_2m_sde', 'dpmpp_sde', 'dpmpp_3m_sde', 'heun', 'dpm_2', 'uni_pc'}
SCHEDULERS = {'normal', 'karras', 'exponential', 'sgm_uniform', 'simple', 'beta'}
JOB_TIMEOUT = 600


class MediaFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _http(method, path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(COMFY_URL + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return raw if path.startswith('/view') else json.loads(raw or b'null')
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b'{}')
        except ValueError:
            detail = {}
        raise MediaFailure('MEDIA_REJECTED') from RuntimeError(str(detail)[:500])
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise MediaFailure('MEDIA_UNAVAILABLE') from e


def models():
    """Checkpoints and LoRAs ComfyUI can see; online False when ComfyUI is not running."""
    try:
        info = _http('GET', '/object_info/CheckpointLoaderSimple')
        loras = _http('GET', '/object_info/LoraLoader')
    except MediaFailure:
        return {'online': False, 'checkpoints': [], 'loras': []}
    pick = lambda d, node, field: d[node]['input']['required'][field][0]
    return {'online': True, 'checkpoints': pick(info, 'CheckpointLoaderSimple', 'ckpt_name'),
            'loras': pick(loras, 'LoraLoader', 'lora_name')}


def normalise(body):
    """Validate a request into generation parameters (raises MediaFailure('INVALID_INPUT'))."""
    def num(key, default, lo, hi, kind=int):
        v = body.get(key, default)
        try:
            v = kind(v)
        except (TypeError, ValueError):
            raise MediaFailure('INVALID_INPUT')
        if not lo <= v <= hi:
            raise MediaFailure('INVALID_INPUT')
        return v
    prompt = str(body.get('prompt') or '').strip()
    negative = str(body.get('negative') or '').strip()
    if not prompt or len(prompt) > 4000 or len(negative) > 2000:
        raise MediaFailure('INVALID_INPUT')
    if content_blocked(prompt) or minor_reference(prompt):
        raise MediaFailure('CONTENT_BLOCKED')
    loras = []
    for item in body.get('loras') or []:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            raise MediaFailure('INVALID_INPUT')
        w = float(item.get('weight', 1.0))
        if not -2 <= w <= 2:
            raise MediaFailure('INVALID_INPUT')
        loras.append({'name': item['name'], 'weight': w})
    if len(loras) > 5:
        raise MediaFailure('INVALID_INPUT')
    sampler = body.get('sampler') or 'dpmpp_2m_sde'
    scheduler = body.get('scheduler') or 'karras'
    if sampler not in SAMPLERS or scheduler not in SCHEDULERS:
        raise MediaFailure('INVALID_INPUT')
    seed = body.get('seed')
    seed = random.randrange(2 ** 48) if seed in (None, '', -1) else num('seed', 0, 0, 2 ** 63 - 1)
    return {'prompt': prompt, 'negative': negative, 'model': str(body.get('model') or ''),
            'width': num('width', 832, 512, 1536) // 8 * 8, 'height': num('height', 1216, 512, 1536) // 8 * 8,
            'steps': num('steps', 30, 1, 80), 'cfg': num('cfg', 5.0, 1.0, 15.0, float),
            'sampler': sampler, 'scheduler': scheduler, 'seed': seed, 'loras': loras,
            # hires: second pass on an upscaled latent (faces in full-body shots); 0 = off
            'hires': num('hires', 0, 0, 2.0, float), 'hires_denoise': num('hires_denoise', 0.45, 0.1, 0.8, float)}


def is_flux(model):
    return 'flux' in (model or '').lower()


def workflow(p):
    """ComfyUI API-format graph for SDXL txt2img with a LoRA chain."""
    g = {'1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': p['model']}}}
    model, clip, n = ['1', 0], ['1', 1], 10
    for lora in p['loras']:
        g[str(n)] = {'class_type': 'LoraLoader', 'inputs': {'model': model, 'clip': clip, 'lora_name': lora['name'],
                                                            'strength_model': lora['weight'], 'strength_clip': lora['weight']}}
        model, clip, n = [str(n), 0], [str(n), 1], n + 1
    negative = ', '.join(x for x in (p['negative'], MINOR_NEGATIVE) if x)
    g.update({
        '2': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': clip, 'text': p['prompt']}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': clip, 'text': negative}},
        '4': {'class_type': 'EmptyLatentImage', 'inputs': {'width': p['width'], 'height': p['height'], 'batch_size': 1}},
        '5': {'class_type': 'KSampler', 'inputs': {'model': model, 'positive': ['2', 0], 'negative': ['3', 0],
              'latent_image': ['4', 0], 'seed': p['seed'], 'steps': p['steps'], 'cfg': p['cfg'],
              'sampler_name': p['sampler'], 'scheduler': p['scheduler'], 'denoise': 1.0}},
        '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['1', 2]}},
        '7': {'class_type': 'PreviewImage', 'inputs': {'images': ['6', 0]}},
    })
    if is_flux(p['model']):
        # FLUX.1-dev: distilled guidance instead of CFG; the negative is ignored at cfg 1.
        g['2b'] = {'class_type': 'FluxGuidance', 'inputs': {'conditioning': ['2', 0], 'guidance': p['cfg']}}
        g['5']['inputs'].update(positive=['2b', 0], cfg=1.0)
    if p.get('hires', 0) >= 1.05:
        g['8'] = {'class_type': 'LatentUpscaleBy', 'inputs': {'samples': ['5', 0], 'upscale_method': 'bislerp', 'scale_by': p['hires']}}
        g['9'] = {'class_type': 'KSampler', 'inputs': dict(g['5']['inputs'], latent_image=['8', 0], denoise=p['hires_denoise'],
                                                            steps=max(10, p['steps'] // 2))}
        g['6']['inputs']['samples'] = ['9', 0]
    return g


def run(p, *, poll=1.0, timeout=JOB_TIMEOUT):
    """Queue the workflow, wait, return PNG bytes."""
    queued = _http('POST', '/prompt', {'prompt': workflow(p), 'client_id': 'ego-core'})
    prompt_id = queued.get('prompt_id') if isinstance(queued, dict) else None
    if not prompt_id:
        raise MediaFailure('MEDIA_REJECTED')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll)
        entry = (_http('GET', '/history/' + prompt_id) or {}).get(prompt_id)
        if not entry:
            continue
        status = entry.get('status') or {}
        if status.get('status_str') == 'error':
            raise MediaFailure('MEDIA_FAILED')
        for out in (entry.get('outputs') or {}).values():
            for img in out.get('images') or []:
                q = urllib.parse.urlencode({'filename': img['filename'], 'subfolder': img.get('subfolder', ''),
                                            'type': img.get('type', 'temp')})
                return _http('GET', '/view?' + q, timeout=30)
        if status.get('completed'):
            raise MediaFailure('MEDIA_FAILED')
    raise MediaFailure('MEDIA_TIMEOUT')


class MediaJobs:
    """One worker thread: ComfyUI and the LLM share the GPU, so jobs run one at a time."""

    def __init__(self, store, runner=None):
        self.store, self.runner = store, runner
        self.jobs, self.lock = {}, threading.Lock()
        self.queue, self.wake = [], threading.Event()
        threading.Thread(target=self._loop, daemon=True, name='ego-media').start()

    def submit(self, body):
        p = normalise(body)
        if not p['model']:
            ckpts = models()['checkpoints']
            if not ckpts:
                raise MediaFailure('MEDIA_UNAVAILABLE')
            p['model'] = ckpts[0]
        job = {'id': uuid.uuid4().hex, 'status': 'queued', 'params': p, 'image_id': None, 'error': None,
               'created': time.time(), 'started': None, 'finished': None}
        with self.lock:
            self.jobs[job['id']] = job
            self.queue.append(job['id'])
            old = [k for k, j in self.jobs.items() if j['finished'] and time.time() - j['finished'] > 3600]
            for k in old:
                self.jobs.pop(k, None)
        self.wake.set()
        return self.view(job['id'])

    def view(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise MediaFailure('NOT_FOUND')
            out = {k: job[k] for k in ('id', 'status', 'image_id', 'error')}
            out['seed'] = job['params']['seed']
            out['position'] = self.queue.index(job_id) + 1 if job_id in self.queue else 0
            end = job['finished'] or time.time()
            out['seconds'] = round(end - (job['started'] or end), 1)
            return out

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
                png = (self.runner or run)(job["params"])
                meta = dict(job['params'], seconds=round(time.time() - job['started'], 1))
                image_id = self.store.put_media_image('image/png', png, meta)
                status, error = 'done', None
            except MediaFailure as e:
                image_id, status, error = None, 'failed', e.code
            except Exception:                              # store failure etc.: report, keep the worker alive
                image_id, status, error = None, 'failed', 'MEDIA_FAILED'
            with self.lock:
                job.update(status=status, image_id=image_id, error=error, finished=time.time())
