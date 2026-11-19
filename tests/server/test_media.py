"""2026-11-08: image generation through ComfyUI — validation, workflow graph, job flow and storage (ComfyUI faked)."""
import time

from server.ego_core import media
from test_core_api import LocalClient, core, uid  # noqa: F401  (fixture reuse)

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


def test_normalise_and_workflow():
    p = media.normalise({'prompt': 'portrait of a woman, 30 years old', 'seed': 7, 'width': 830,
                         'loras': [{'name': 'a.safetensors', 'weight': 0.6}, {'name': 'b.safetensors', 'weight': 0.3}]})
    assert p['width'] == 824 and p['seed'] == 7 and p['steps'] == 30
    g = media.workflow(dict(p, model='m.safetensors'))
    assert g['10']['inputs']['model'] == ['1', 0] and g['11']['inputs']['model'] == ['10', 0]
    assert g['5']['inputs']['model'] == ['11', 0] and g['2']['inputs']['clip'] == ['11', 1]
    assert 'underage' in g['3']['inputs']['text']
    for bad in ({}, {'prompt': 'x', 'steps': 500}, {'prompt': 'x', 'sampler': 'nope'},
                {'prompt': 'x', 'loras': [{'name': 'a', 'weight': 9}]}):
        try:
            media.normalise(bad)
        except media.MediaFailure as e:
            assert e.code == 'INVALID_INPUT'
        else:
            raise AssertionError(bad)


def test_generation_job_stores_image(core, monkeypatch):
    c, service, store, *_ = core
    seen = []
    monkeypatch.setattr(media, 'run', lambda p: seen.append(p) or PNG)
    r = c.post('/v1/media/images', json={'prompt': 'woman in a red dress', 'model': 'm.safetensors', 'seed': 42})
    assert r.status_code == 202
    job = r.json()
    for _ in range(50):
        job = c.get('/v1/media/jobs/' + job['id']).json()
        if job['status'] in ('done', 'failed'):
            break
        time.sleep(0.05)
    assert job['status'] == 'done' and job['seed'] == 42 and seen[0]['model'] == 'm.safetensors'
    assert c.request('GET', '/v1/media/images/' + job['image_id']).content == PNG
    listed = c.get('/v1/media/images').json()['images']
    assert listed[0]['id'] == job['image_id'] and listed[0]['params']['prompt'] == 'woman in a red dress'
    assert c.get('/v1/media/jobs/nope').status_code == 404
    assert c.post('/v1/media/images', json={'prompt': ''}).status_code == 422


def test_comfyui_offline(monkeypatch):
    monkeypatch.setattr(media, 'COMFY_URL', 'http://127.0.0.1:9')
    assert media.models() == {'online': False, 'checkpoints': [], 'loras': []}


def test_flux_workflow_uses_guidance():
    p = dict(media.normalise({'prompt': 'woman, 30 years old', 'cfg': 3.5, 'sampler': 'euler', 'scheduler': 'simple'}), model='flux1-dev-fp8.safetensors')
    g = media.workflow(p)
    assert g['2b']['inputs']['guidance'] == 3.5 and g['5']['inputs']['cfg'] == 1.0 and g['5']['inputs']['positive'] == ['2b', 0]


def test_delete_media_image(core, monkeypatch):
    c, service, store, *_ = core
    image_id = store.put_media_image('image/png', PNG, {'prompt': 'x'})
    assert c.request('GET', '/v1/media/images/' + image_id).status_code == 200
    assert c.request('DELETE', '/v1/media/images/' + image_id).json() == {'id': image_id, 'deleted': True}
    assert c.request('GET', '/v1/media/images/' + image_id).status_code == 404
    assert c.request('DELETE', '/v1/media/images/' + image_id).status_code == 404
