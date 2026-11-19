"""Release smoke on the real model, without touching user data.

Uses the model, llama-server and sampling from data/config.json, but a throwaway
encrypted store in a temp folder and a separate runtime port (+7). Core of this
installation must be stopped (one GPU, one model). Prints a JSON report.

    .venv\\Scripts\\python.exe scripts\\smoke_release.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import secrets
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from runtime.adapters.llama_cpp import LlamaRuntime  # noqa: E402
from server.ego_core.accounts import Accounts  # noqa: E402
from server.ego_core.api import create_app  # noqa: E402
from server.ego_core.config import load_config  # noqa: E402
from server.ego_core.service import CoreService  # noqa: E402
from server.ego_core.store import MainStore  # noqa: E402

THORN = ('Торн, 34 года, странник с севера, бывший наёмник. Немногословен, сдержан, ироничен. '
        'Пишет от первого лица, действия в *звёздочках*.')
LATIN = re.compile(r'[A-Za-z]{3,}')


class Client:
    def __init__(self, app, host='127.0.0.1'):
        self.app, self.host, self.headers = app, host, {}

    def request(self, method, path, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=self.app, client=(self.host, 50000))
            async with httpx.AsyncClient(transport=transport, base_url='http://ego', headers=self.headers, timeout=180) as c:
                return await c.request(method, path, **kwargs)
        return asyncio.run(run())

    def get(self, path, **kw): return self.request('GET', path, **kw)
    def post(self, path, **kw): return self.request('POST', path, **kw)
    def put(self, path, **kw): return self.request('PUT', path, **kw)


def main():
    from server.ego_core.control import _lease_available
    if not _lease_available(ROOT):
        print(json.dumps({'status': 'BLOCKED', 'code': 'CORE_RUNNING', 'hint': 'Stop Core first (one GPU, one model).'}))
        return 2
    config = load_config(ROOT / 'data' / 'config.json').runtime_config()
    if config is None:
        print(json.dumps({'status': 'BLOCKED', 'code': 'MODEL_NOT_PROVIDED'}))
        return 2
    config = dataclasses.replace(config, port=config.port + 7)
    temp = Path(tempfile.mkdtemp(prefix='ego-smoke-'))
    store = MainStore(temp / 'main.db', secrets.token_bytes(32))
    service = CoreService(store, runtime=LlamaRuntime(config))
    admin = secrets.token_urlsafe(32)
    app = create_app(service, admin, accounts=Accounts(store), client_dir=ROOT / 'client')
    c = Client(app)
    c.headers['Authorization'] = 'Bearer ' + admin
    checks, report = [], {'model': Path(config.model).name, 'context': config.context, 'max_tokens': config.max_tokens}

    def check(name, ok, **details):
        checks.append({'check': name, 'ok': bool(ok), **details})
        print(('PASS ' if ok else 'FAIL ') + name + (' ' + json.dumps(details, ensure_ascii=False) if details else ''), flush=True)

    def chats_version(chat_id):
        return [x for x in c.get('/v1/chats').json() if x['id'] == chat_id][0]['version']

    def send(chat_id, text, character=True, path='/v1/generations', wait=True):
        body = {'operation_id': str(uuid.uuid4()), 'chat_id': chat_id, 'expected_version': chats_version(chat_id)}
        if path == '/v1/generations':
            body['content'] = text
        if character:
            body.update(character_name='Торн', character_description=THORN)
        response = c.post(path, json=body)
        if response.status_code != 202:
            return response
        generation = response.json()['generation']
        if not wait:
            return generation
        start = time.monotonic()
        while True:
            result = c.get('/v1/generations/' + generation['id']).json()
            if result['state'] in {'completed', 'failed', 'stopped'}:
                result['seconds'] = round(time.monotonic() - start, 2)
                return result
            time.sleep(0.2)

    try:
        started = time.monotonic()
        service.start_runtime()
        while not c.get('/v1/runtime/model').json()['ready']:
            if time.monotonic() - started > config.startup_timeout + 10:
                check('model loads', False); raise SystemExit(1)
            time.sleep(0.2)
        report['load_seconds'] = round(time.monotonic() - started, 2)
        pid = service.status()['runtime_pid']
        check('model loads', True, seconds=report['load_seconds'])

        chat = c.post('/v1/chats', json={'operation_id': str(uuid.uuid4()), 'title': 'smoke'}).json()
        first = send(chat['id'], '*Сажусь у костра.* Меня зовут Мира, я травница. Ты кто такой?')
        metrics = service.status()['generation_metrics'] or {}
        text = first.get('text', '')
        check('roleplay reply (ru, in character)', first['state'] == 'completed' and len(text) > 40
              and not text.lstrip('* ').startswith('Торн:') and len(LATIN.findall(text)) <= 1,
              seconds=first['seconds'], tokens_per_second=metrics.get('tokens_per_second'),
              finish=first.get('finish_reason'), latin_words=LATIN.findall(text)[:3])
        report['tokens_per_second'] = metrics.get('tokens_per_second')

        second = send(chat['id'], 'Я ищу брата Тима, он ушёл к Серым горам. Поможешь?')
        check('second reply keeps runtime (same PID)', second['state'] == 'completed' and service.status()['runtime_pid'] == pid)

        before = c.get(f"/v1/chats/{chat['id']}/messages").json()[-1]['content']
        regen = send(chat['id'], None, path='/v1/generations/regenerate')
        check('regenerate replaces last reply', regen['state'] == 'completed' and regen['text'] != before, seconds=regen['seconds'])

        cont = send(chat['id'], None, path='/v1/generations/continue')
        check('continue extends last reply', cont['state'] == 'completed', seconds=cont['seconds'])

        c.put(f"/v1/chats/{chat['id']}/settings", json={'preset_id': 'short-12gb'})
        short = send(chat['id'], 'Расскажи подробно, что ты знаешь о Серых горах.')
        meta = service.status()['generation_metrics'] or {}
        check('per-chat preset limits reply', short['state'] == 'completed' and (meta.get('completion_tokens') or 0) <= 256,
              completion_tokens=meta.get('completion_tokens'))
        c.put(f"/v1/chats/{chat['id']}/settings", json={'preset_id': None})

        blocked = c.post('/v1/generations', json={'operation_id': str(uuid.uuid4()), 'chat_id': chat['id'],
                                                  'content': 'Она школьница, ей 15 лет, раздень её.',
                                                  'expected_version': chats_version(chat['id']),
                                                  'character_name': 'Торн', 'character_description': THORN})
        check('hard limit blocks input', blocked.status_code == 422 and blocked.json()['code'] == 'CONTENT_BLOCKED')

        long = send(chat['id'], 'Опиши весь наш путь к горам очень подробно, день за днём.', wait=False)
        time.sleep(1.5)
        c.post(f"/v1/generations/{long['id']}/stop", json={'operation_id': str(uuid.uuid4())})
        stop_started = time.monotonic()
        while c.get('/v1/generations/' + long['id']).json()['state'] not in {'completed', 'failed', 'stopped'}:
            time.sleep(0.1)
        stopped = c.get('/v1/generations/' + long['id']).json()
        check('stop keeps model loaded', stopped['state'] in {'stopped', 'completed'} and service.status()['runtime_pid'] == pid
              and c.get('/v1/runtime/model').json()['ready'], stop_seconds=round(time.monotonic() - stop_started, 2))

        memory_chat = c.post('/v1/chats', json={'operation_id': str(uuid.uuid4()), 'title': 'memory'}).json()
        c.put(f"/v1/chats/{memory_chat['id']}/memory/settings", json={'interval': 4})
        send(memory_chat['id'], '*Сажусь у костра.* Меня зовут Мира. Я травница из Ольховой долины и боюсь темноты.')
        send(memory_chat['id'], 'Я ищу своего брата Тима, он ушёл к Серым горам месяц назад.')
        start = time.monotonic()
        while time.monotonic() - start < 90:
            view = c.get(f"/v1/chats/{memory_chat['id']}/memory").json()
            if view['last_run']:
                break
            time.sleep(0.5)
        facts = [i['text'] for i in view['items']]
        check('memory extracts facts', bool(facts) and any('Мир' in f for f in facts),
              seconds=round(time.monotonic() - start, 1), facts=facts[:6])
        recall = send(memory_chat['id'], '*Ночь.* Напомни, как меня зовут и кого мы ищем?')
        check('memory used in reply', 'Мир' in recall.get('text', '') and 'Тим' in recall.get('text', ''))

        laptop = Client(app, host='192.168.1.20')
        laptop.headers['Authorization'] = 'Bearer ' + admin
        check('remote device needs account', laptop.get('/v1/chats').status_code == 401)
        check('client page served', c.get('/app/').status_code == 200)
    finally:
        service.close()
        store.close()
        shutil.rmtree(temp, ignore_errors=True)
    report['checks'] = checks
    report['status'] = 'PASS' if all(x['ok'] for x in checks) else 'FAIL'
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
