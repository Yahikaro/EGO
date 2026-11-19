"""Local test generation. Chat contents are never sent to diagnostics or cloud APIs."""
from __future__ import annotations
import time
import threading
from uuid import uuid4
from server.ego_core import control

TERMINAL = {'completed', 'stopped', 'failed'}

def list_chats(root):
    return control.request(root, 'GET', '/v1/chats', timeout=5)


def history(root, chat_id):
    return control.request(root, 'GET', f'/v1/chats/{chat_id}/messages', timeout=5)


def new_chat(root):
    return control.request(root, 'POST', '/v1/chats', payload={
        'operation_id': str(uuid4()), 'title': 'EGO local playground'}, timeout=5)


def chat_version(root, chat_id):
    chats = list_chats(root)
    chat = next((row for row in chats if row['id'] == chat_id), None)
    if chat is None:
        raise ValueError('CHAT_NOT_FOUND')
    return chat['version']


def _generation_updates(root, generation_id, deadline):
    """Prefer authenticated SSE; use the existing polling protocol on failure."""
    try:
        for snapshot in control.generation_event_stream(root,generation_id):
            yield snapshot
            if snapshot.get('state') in TERMINAL:
                return
    except Exception:
        # Any SSE problem (network, vault, parser) falls back to the polling
        # protocol instead of aborting an already accepted generation.
        pass
    while time.monotonic()<deadline:
        snapshot=control.request(root,'GET',f'/v1/generations/{generation_id}',timeout=5)
        yield snapshot
        if snapshot.get('state') in TERMINAL: return
        time.sleep(0.12)


def generate(root, prompt, progress, cancel, timeout=300, chat_id=None, character_name=None, character_description=None, regenerate=False, continuing=False):
    prompt = prompt.strip()
    if (not regenerate and not continuing and not prompt) or len(prompt) > 32768:
        return {'status': 'FAIL', 'code': 'INVALID_PROMPT'}
    generation_id = None
    start = time.monotonic()
    try:
        state = control.request(root, 'GET', '/v1/system/status', timeout=5)
        if state.get('runtime') == 'unconfigured':
            return {'status': 'BLOCKED', 'code': 'MODEL_NOT_PROVIDED'}
        # Core serializes concurrent requests; an active generation does not
        # block a different chat from joining its durable queue.
        if cancel.is_set(): return {'status': 'CANCELLED', 'code': 'TASK_CANCELLED'}
        if (regenerate or continuing) and not chat_id:
            return {'status':'FAIL','code':'NOTHING_TO_REGENERATE'}
        chat = ({'id': chat_id, 'version': chat_version(root, chat_id)}
                if chat_id else new_chat(root))
        payload={'operation_id': str(uuid4()), 'chat_id': chat['id'],
                 'expected_version': chat['version'], 'character_name': character_name,
                 'character_description': character_description}
        if not regenerate and not continuing: payload['content']=prompt
        response = control.request(root, 'POST',
            '/v1/generations/continue' if continuing else ('/v1/generations/regenerate' if regenerate else '/v1/generations'),
            payload=payload, timeout=10)
        generation_id = response['generation']['id']
        deadline = time.monotonic() + timeout
        terminal_event=threading.Event()
        def interrupt():
            while not terminal_event.is_set():
                if cancel.wait(0.1):
                    if terminal_event.is_set(): return
                    try:
                        control.request(root,'POST',f'/v1/generations/{generation_id}/stop',
                                        payload={'operation_id':str(uuid4())},timeout=5)
                    except Exception: pass
                    return
        threading.Thread(target=interrupt,daemon=True,name='ego-playground-cancel').start()
        try:
            for result in _generation_updates(root,generation_id,deadline):
                text=result.get('text','')
                progress({'text':text,'state':result.get('state','queued')})
                if result.get('state') in TERMINAL:
                    try:
                        meta=control.request(root,'GET','/v1/system/status',timeout=5).get('generation_metrics') or {}
                        meta=meta if meta.get('generation_id')==generation_id else {}
                    except Exception: meta={}
                    state=result['state']
                    return {'status':'PASS' if state=='completed' else ('CANCELLED' if cancel.is_set() else 'FAIL'),
                            'code':'PLAYGROUND_COMPLETE' if state=='completed' else ('GENERATION_STOP_REQUESTED' if cancel.is_set() else (result.get('error_code') or 'GENERATION_STOPPED')),
                            'seconds':round(time.monotonic()-start,2),
                            'characters':len(text),'chat_id':chat['id'],'finish_reason':meta.get('finish_reason'),
                            'metrics':meta,'generation_id':generation_id}
        finally:
            terminal_event.set()
        control.request(root,'POST',f'/v1/generations/{generation_id}/stop',
                        payload={'operation_id':str(uuid4())},timeout=5)
        return {'status':'FAIL','code':'PLAYGROUND_TIMEOUT'}
    except Exception as exc:
        # Exception text may contain content or local secrets.
        return {'status':'FAIL','code':type(exc).__name__}
