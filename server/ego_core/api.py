"""Versioned local administration API. LAN/device pairing is the M2 boundary."""
from __future__ import annotations
import asyncio
import hmac
import hashlib
import re
import json
import secrets
import threading
import time
from fastapi import Depends, FastAPI, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from server.ego_core.store import StoreFailure
from server.ego_core.service import CoreFailure, TERMINAL
from server.ego_core.config import CoreConfig
from server.ego_core.characters import compose_profile, find_world
from server.ego_core.content_guard import input_blocked, minor_reference, nonconsent_reference
from server.ego_core.errors import CATALOG as ERROR_CATALOG, describe as describe_error
from server.ego_core import __version__
from server.ego_core import media as media_gen
from server.ego_core import tts as tts_gen


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation_id: str = Field(min_length=36, max_length=36)


class ChatCommand(Command):
    title: str = Field(min_length=1, max_length=200)


class GenerationCommand(Command):
    chat_id: str = Field(min_length=36, max_length=36)
    content: str = Field(min_length=1, max_length=32768)
    expected_version: int = Field(ge=1)
    character_name: str | None = Field(default=None, max_length=120)
    character_description: str | None = Field(default=None, max_length=4000)
    character_id: str | None = Field(default=None, min_length=1, max_length=64)


class RegenerateCommand(Command):
    chat_id: str = Field(min_length=36, max_length=36)
    expected_version: int = Field(ge=1)
    character_name: str | None = Field(default=None, max_length=120)
    character_description: str | None = Field(default=None, max_length=4000)
    character_id: str | None = Field(default=None, min_length=1, max_length=64)


class ChatSettingsCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    preset_id: str | None = Field(default=None, min_length=1, max_length=64)


class RecordCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    data: dict
    expected_version: int | None = Field(default=None, ge=0)



# Hard content limits live in content_guard.py (shared with the generation loop).
_minor_reference = minor_reference
_nonconsent_reference = nonconsent_reference


def error_body(code):
    """Fixed catalog texts only (never request content); `code` stays the contract."""
    message, hint = describe_error(code)
    return {'code': code, 'message': message, 'hint': hint}


class RequestTooLarge(Exception): pass


_AVATAR_PATH = re.compile(r'/v1/characters/[A-Za-z0-9_-]{1,64}/avatar|/v1/profile/avatar')
_RECORD_PATH = re.compile(r'/v1/(characters|worlds)/[A-Za-z0-9_-]{1,64}')


def body_limit(path):
    if _AVATAR_PATH.fullmatch(path):
        return 2 * 1024 * 1024 + 1024
    if _RECORD_PATH.fullmatch(path):
        return 256 * 1024
    if path == '/v1/tts/voices':                 # base64 WAV sample, <= 6 MB raw (tts.check_sample)
        return 8 * 1024 * 1024 + 64 * 1024
    return 65536


class BodyLimit:
    def __init__(self, app, error_callback=None):
        self.app, self.error_callback = app, error_callback
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        limit = body_limit(scope.get('path', ''))
        try:
            if int(headers.get(b'content-length', b'0')) > limit:
                raise RequestTooLarge()
        except (ValueError, RequestTooLarge):
            if self.error_callback: self.error_callback('REQUEST_TOO_LARGE')
            return await JSONResponse(error_body('REQUEST_TOO_LARGE'), status_code=413)(scope, receive, send)
        if scope['method'] not in {'POST', 'PUT', 'PATCH'}:
            return await self.app(scope, receive, send)
        # Prebuffer only a bounded body. FastAPI converts arbitrary receive
        # exceptions to parse errors, so enforce limits before its JSON parser.
        data = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            data.extend(event.get('body', b''))
            if len(data) > limit:
                if self.error_callback: self.error_callback('REQUEST_TOO_LARGE')
                return await JSONResponse(error_body('REQUEST_TOO_LARGE'), status_code=413)(scope, receive, send)
            if not event.get('more_body', False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request','body':bytes(data),'more_body':False}
            return await receive()
        return await self.app(scope, replay, send)


class ProfileCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    profile: dict


class ChatMetaCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    meta: dict
    title: str | None = Field(default=None, min_length=1, max_length=200)


class MemorySettingsCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool | None = None
    interval: int | None = Field(default=None, ge=4, le=200)


class EnchantCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: str = Field(pattern='^(world|character)$')
    field: str = Field(min_length=1, max_length=40)
    text: str = Field(default='', max_length=6000)
    context: dict[str, str] = Field(default_factory=dict)


class MemoryItemCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    text: str = Field(min_length=1, max_length=300)
    pinned: bool = False


class MemoryEditCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    text: str | None = Field(default=None, min_length=1, max_length=1500)
    pinned: bool | None = None


_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


class AccountCredentials(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class AccountNewUser(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class AccountRemoveUser(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    email: str = Field(min_length=3, max_length=254)


class AccountProfileCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    profile: dict


_LOOPBACK = {'127.0.0.1', '::1'}


def _is_local(request):
    return bool(request.client and request.client.host in _LOOPBACK)


def _device(request):
    """Short device label from the User-Agent (no full fingerprint is stored)."""
    agent = request.headers.get('user-agent', '')[:400]
    system = next((name for key, name in (('Windows', 'Windows'), ('Android', 'Android'), ('iPhone', 'iPhone'),
                   ('iPad', 'iPad'), ('Mac OS', 'macOS'), ('Linux', 'Linux')) if key in agent), 'устройство')
    browser = next((name for key, name in (('Edg/', 'Edge'), ('OPR/', 'Opera'), ('YaBrowser', 'Яндекс'),
                    ('Firefox/', 'Firefox'), ('Chrome/', 'Chrome'), ('Safari/', 'Safari')) if key in agent), 'браузер')
    return f'{system} · {browser}'


_ACCOUNT_STATUS = {'ACCOUNT_INVALID_CREDENTIALS': 401, 'ACCOUNT_LOCKED': 429, 'ACCOUNT_RATE_LIMITED': 429, 'ACCOUNT_EXISTS': 409,
                   'ACCOUNT_WEAK_PASSWORD': 422, 'ACCOUNT_INVALID_EMAIL': 422,
                   'ACCOUNTS_UNAVAILABLE': 503,
                   'MEMORY_UNAVAILABLE': 503}
# Expected user mistakes; they must not replace the last actionable Core error.
_QUIET = {'UNAUTHORIZED', 'ACCOUNT_INVALID_CREDENTIALS',
          'ACCOUNT_WEAK_PASSWORD', 'ACCOUNT_INVALID_EMAIL', 'ACCOUNT_EXISTS'}


# Privacy stage: the client is local-only (no third-party requests) and story text
# must not linger in browser caches, especially on a laptop that signs out.
CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                           "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
                           "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
_COMMON_HEADERS = [(b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer'),
                   (b'x-frame-options', b'DENY'), (b'cross-origin-opener-policy', b'same-origin'),
                   (b'cross-origin-resource-policy', b'same-origin'),
                   (b'permissions-policy', b'camera=(), microphone=(), geolocation=(), payment=(), usb=()'),
                   (b'content-security-policy', CONTENT_SECURITY_POLICY.encode())]


class PrivacyHeaders:
    """ASGI wrapper (keeps SSE streaming intact): security headers everywhere,
    `no-store` for API responses, revalidation for the static client."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope.get('path', '')
        cache = b'no-store' if not path.startswith('/app') else b'no-cache'

        async def wrapped(message):
            if message['type'] == 'http.response.start':
                headers = [(k, v) for k, v in message.get('headers', [])
                           if k.lower() not in {h for h, _ in _COMMON_HEADERS} and k.lower() != b'cache-control']
                headers += _COMMON_HEADERS + [(b'cache-control', cache)]
                if cache == b'no-store':
                    headers.append((b'pragma', b'no-cache'))
                message = {**message, 'headers': headers}
            await send(message)
        return await self.app(scope, receive, wrapped)


class LanguageScope:
    """ASGI wrapper (2026-11-06): the request's language (X-EGO-Lang, else Accept-Language)
    for error texts and emails; see languages.py."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from server.ego_core import languages
        headers = {k.lower(): v for k, v in scope.get('headers', [])}
        chosen = headers.get(b'x-ego-lang', b'').decode('latin-1')
        if chosen:
            languages.set_owner(chosen)   # the client's interface language is the model's language too
        token = languages.use(languages.from_headers(chosen, headers.get(b'accept-language', b'').decode('latin-1')))
        try:
            return await self.app(scope, receive, send)
        finally:
            languages.reset(token)


class ClientSessionCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    code: str = Field(min_length=32, max_length=128)


CLIENT_CODE_TTL = 120.0  # Seconds a one-time client launch code stays valid.
# Client tokens may use chat/generation data routes but never mint launch codes
# or touch Core/Runtime lifecycle.
_ADMIN_ONLY = {'/v1/client/launch', '/v1/system/shutdown', '/v1/runtime/restart', '/v1/runtime/configure',
               '/v1/tunnel', '/v1/tunnel/start', '/v1/tunnel/stop',
               '/v1/accounts/users', '/v1/accounts/users/remove', '/v1/accounts/users/password'}


def create_app(service, admin_token, *, shutdown_callback=None, error_callback=None, client_dir=None, accounts=None,
               network=None, tunnel=None):
    if not isinstance(admin_token, str) or len(admin_token) < 43:
        raise ValueError('STRONG_ADMIN_TOKEN_REQUIRED')
    app = FastAPI(title='EGO local Server Core', version=__version__,
                  docs_url=None, redoc_url=None, openapi_url=None)
    # In-memory only: a Core restart invalidates every client session.
    launch_codes, client_tokens = {}, set()
    client_lock = threading.Lock()

    def record_error(code):
        service.last_api_error = {'code': code}
        if error_callback is not None:
            try:
                error_callback(code)
            except Exception:
                service.last_api_error['diagnostics'] = 'UNAVAILABLE'

    app.add_middleware(BodyLimit, error_callback=record_error)
    app.add_middleware(PrivacyHeaders)
    app.add_middleware(LanguageScope)

    def authorized(request: Request):
        header = request.headers.get('authorization', '')
        return len(header) <= 256 and hmac.compare_digest(header.encode('utf-8'), ('Bearer '+admin_token).encode())

    def client_authorized(request: Request):
        header = request.headers.get('authorization', '')
        if len(header) > 256 or not header.startswith('Bearer '):
            return False
        supplied = header[7:].encode('utf-8')
        with client_lock:
            tokens = list(client_tokens)
        return any(hmac.compare_digest(supplied, token.encode()) for token in tokens)

    def account_authorized(request: Request):
        header = request.headers.get('authorization', '')
        if accounts is None or len(header) > 256 or not header.startswith('Bearer '):
            return False
        if accounts.session_user(header[7:]) is None:
            return False
        request.state.account_token = header[7:]
        return True

    def require_auth(request: Request):
        if not _is_local(request):
            # Stage 4i: another device on the home network may only use an
            # account session; admin, launch-code and lifecycle stay on the PC.
            if request.url.path in _ADMIN_ONLY or not account_authorized(request):
                raise CoreFailure('UNAUTHORIZED')
            return
        if not authorized(request):
            if request.url.path in _ADMIN_ONLY or not (client_authorized(request) or account_authorized(request)):
                raise CoreFailure('UNAUTHORIZED')
            return
        owner=getattr(service,'installation_id',None)
        if owner is not None:
            supplied=request.headers.get('X-EGO-Installation')
            lifecycle=request.url.path in {'/v1/system/shutdown','/v1/runtime/restart','/v1/runtime/configure'}
            # Preserve existing bearer-only data clients. Lifecycle mutations
            # additionally prove the installation; reject any foreign header.
            if (supplied is None and lifecycle) or (supplied is not None and
                    (len(supplied)>128 or not hmac.compare_digest(supplied.encode('utf-8'),owner.encode('utf-8')))):
                raise CoreFailure('CORE_INSTALLATION_MISMATCH')

    @app.exception_handler(CoreFailure)
    @app.exception_handler(StoreFailure)
    async def failure(request, exc):
        code = exc.code
        # An unauthenticated local browser probe is expected; it must not
        # replace the application's last actionable Core error.
        if code not in _QUIET:
            record_error(code)
        status = _ACCOUNT_STATUS[code] if code in _ACCOUNT_STATUS else 401 if code == 'UNAUTHORIZED' else (403 if code=='CORE_INSTALLATION_MISMATCH' else (404 if code in {'NOT_FOUND','OPERATION_NOT_FOUND'} else
                 (422 if code in {'INVALID_ID','INVALID_INPUT','CONFIG_INVALID','CONTENT_BLOCKED'} else 409)))
        return JSONResponse(error_body(code), status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        record_error('INVALID_REQUEST')
        return JSONResponse(error_body('INVALID_REQUEST'), status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        record_error('INTERNAL_ERROR')
        return JSONResponse(error_body('INTERNAL_ERROR'), status_code=500)

    @app.exception_handler(RequestTooLarge)
    async def too_large(request, exc):
        return JSONResponse(error_body('REQUEST_TOO_LARGE'),status_code=413)

    @app.get('/health')
    def health(): return {'status':'ok','stage':'M1'}

    @app.get('/v1/system/status', dependencies=[Depends(require_auth)])
    def status(): return service.status()

    @app.get('/v1/runtime/model', dependencies=[Depends(require_auth)])
    def model_metadata(): return service.model_info()

    @app.post('/v1/runtime/restart', status_code=202, dependencies=[Depends(require_auth)])
    def restart_runtime(): return service.restart_runtime()

    @app.post('/v1/runtime/configure', status_code=202, dependencies=[Depends(require_auth)])
    def configure_runtime(body: CoreConfig):
        # This endpoint updates the owned runtime, never keys/databases or the
        # Core listener. The existing configuration manager owns disk rollback.
        return service.reconfigure_runtime(body.runtime_config())

    @app.get('/v1/openapi.json', dependencies=[Depends(require_auth)])
    def schema(): return app.openapi()

    @app.get('/v1/chats', dependencies=[Depends(require_auth)])
    def chats(): return service.store.list_chats()

    @app.post('/v1/chats', dependencies=[Depends(require_auth)])
    def create_chat(body: ChatCommand):
        return service.store.create_chat(body.operation_id, body.title)

    @app.get('/v1/chats/{chat_id}/messages', dependencies=[Depends(require_auth)])
    def messages(chat_id: str, before: str | None = None, limit: int = Query(default=100, ge=1, le=100)):
        # Additive M1.7 paging; no parameters keeps the M1 "latest 100" contract.
        if before is None and limit == 100:
            return service.store.messages(chat_id)
        return service.store.messages(chat_id, before=before, limit=limit)

    def with_character(body):
        values = body.model_dump()
        character_id = values.pop('character_id')
        if input_blocked(values.get('content'), values.get('character_name'), values.get('character_description')):
            raise CoreFailure('CONTENT_BLOCKED')
        if character_id is None:
            return values
        if values['character_name'] is not None or values['character_description'] is not None:
            raise CoreFailure('INVALID_INPUT')  # One source of truth per request.
        character = service.store.get_record('character', character_id)['data']
        world = find_world(character, service.store.list_records('world'))
        values['character_name'], values['character_description'] = compose_profile(character, world)
        if input_blocked(values.get('content'), values['character_name'], values['character_description']):
            raise CoreFailure('CONTENT_BLOCKED')
        return values

    @app.post('/v1/generations', status_code=202, dependencies=[Depends(require_auth)])
    def generate(body: GenerationCommand):
        generation = service.generate(**with_character(body))
        return {'ack':'persisted','generation':generation}

    @app.post('/v1/generations/regenerate', status_code=202, dependencies=[Depends(require_auth)])
    def regenerate(body: RegenerateCommand):
        generation = service.regenerate(**with_character(body))
        return {'ack':'persisted','generation':generation}

    @app.post('/v1/generations/continue', status_code=202, dependencies=[Depends(require_auth)])
    def continue_generation(body: RegenerateCommand):
        generation=service.continue_generation(**with_character(body))
        return {'ack':'persisted','generation':generation}

    @app.get('/v1/generations/{generation_id}', dependencies=[Depends(require_auth)])
    def generation(generation_id: str): return service.store.generation(generation_id)

    @app.post('/v1/generations/{generation_id}/stop', status_code=202, dependencies=[Depends(require_auth)])
    def stop(generation_id: str, body: Command): return service.stop(body.operation_id, generation_id)

    @app.get('/v1/operations/{operation_id}', dependencies=[Depends(require_auth)])
    def operation(operation_id: str): return service.store.operation(operation_id)

    @app.get('/v1/events', dependencies=[Depends(require_auth)])
    def events(after: int = 0):
        return {'events': service.store.events(after)}

    @app.get('/v1/generations/{generation_id}/events', dependencies=[Depends(require_auth)])
    async def stream(generation_id: str, request: Request):
        service.store.generation(generation_id)  # Validate before sending HTTP headers.
        cursor = request.headers.get('last-event-id', '')
        if cursor and not re.fullmatch(r'[a-f0-9]{64}', cursor):
            return JSONResponse(error_body('INVALID_EVENT_CURSOR'), status_code=422)
        async def snapshots():
            previous = cursor or None
            heartbeat_at = asyncio.get_running_loop().time() + 10.0
            while not await request.is_disconnected():
                current = await asyncio.to_thread(service.store.generation, generation_id)
                encoded = json.dumps(current, ensure_ascii=True, separators=(',', ':'))
                event_id = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
                if event_id != previous:
                    yield 'id: '+event_id+'\nevent: generation\ndata: '+encoded+'\n\n'
                    previous = event_id
                if current['state'] in TERMINAL:
                    return
                if asyncio.get_running_loop().time() >= heartbeat_at:
                    yield ': keepalive\n\n'
                    heartbeat_at = asyncio.get_running_loop().time() + 10.0
                await asyncio.sleep(.1)
        return StreamingResponse(snapshots(), media_type='text/event-stream',
                                 headers={'Cache-Control':'no-store', 'X-Accel-Buffering':'no'})

    @app.get('/v1/characters', dependencies=[Depends(require_auth)])
    def characters(): return {'characters': service.store.list_records('character')}

    @app.put('/v1/characters/{character_id}', dependencies=[Depends(require_auth)])
    def put_character(character_id: str, body: RecordCommand):
        return service.store.put_record('character', character_id, body.data, body.expected_version)

    @app.delete('/v1/characters/{character_id}', dependencies=[Depends(require_auth)])
    def delete_character(character_id: str, expected_version: int | None = Query(default=None, ge=0)):
        return service.store.delete_record('character', character_id, expected_version)

    @app.get('/v1/characters/{character_id}/avatar', dependencies=[Depends(require_auth)])
    def get_avatar(character_id: str):
        media_type, data = service.store.get_avatar(character_id)
        return Response(data, media_type=media_type, headers={'Cache-Control': 'no-store'})

    @app.put('/v1/characters/{character_id}/avatar', dependencies=[Depends(require_auth)])
    async def put_avatar(character_id: str, request: Request):
        media_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
        data = await request.body()
        return await asyncio.to_thread(service.store.put_avatar, character_id, media_type, data)

    @app.delete('/v1/characters/{character_id}/avatar', dependencies=[Depends(require_auth)])
    def delete_avatar(character_id: str): return service.store.delete_avatar(character_id)

    # --- local image generation through ComfyUI (media.py), 2026-11-08 ---
    media_state = {}

    def media_jobs():                       # created on first use (one worker thread per app)
        if 'jobs' not in media_state:
            media_state['jobs'] = media_gen.MediaJobs(service.store)
        return media_state['jobs']

    def media_call(name, *args):
        try:
            return getattr(media_jobs(), name)(*args)
        except media_gen.MediaFailure as e:
            raise CoreFailure(e.code) from None

    @app.get('/v1/media/models', dependencies=[Depends(require_auth)])
    def media_models(): return media_gen.models()

    @app.post('/v1/media/images', status_code=202, dependencies=[Depends(require_auth)])
    def media_generate(body: dict): return media_call('submit', body)

    @app.get('/v1/media/jobs/{job_id}', dependencies=[Depends(require_auth)])
    def media_job(job_id: str): return media_call('view', job_id)

    @app.get('/v1/media/images', dependencies=[Depends(require_auth)])
    def media_images(): return {'images': service.store.list_media_images()}

    @app.delete('/v1/media/images/{image_id}', dependencies=[Depends(require_auth)])
    def media_delete(image_id: str): return service.store.delete_media_image(image_id)

    @app.get('/v1/media/images/{image_id}', dependencies=[Depends(require_auth)])
    def media_image(image_id: str):
        media_type, data = service.store.get_media_image(image_id)
        return Response(data, media_type=media_type, headers={'Cache-Control': 'private, max-age=86400'})

    # --- local text-to-speech (tts.py + separate worker process), 2026-11-08 ---
    def tts_jobs():
        if 'tts' not in media_state:
            media_state['tts'] = tts_gen.TtsJobs(service.store)
        return media_state['tts']

    def tts_call(fn, *args):
        try:
            return fn(*args)
        except tts_gen.TtsFailure as e:
            raise CoreFailure(e.code) from None

    @app.get('/v1/tts/status', dependencies=[Depends(require_auth)])
    def tts_status(): return tts_gen.status()

    @app.get('/v1/tts/voices', dependencies=[Depends(require_auth)])
    def tts_voices():
        builtin = [{k: v for k, v in voice.items() if k != 'speaker'} | {'builtin': True,
                    'demo': tts_gen.demo_path(voice['id']) is not None} for voice in tts_gen.builtin_voices()]
        return {'voices': builtin + [v | {'builtin': False, 'demo': True} for v in service.store.list_tts_voices()]}

    @app.post('/v1/tts/voices', status_code=201, dependencies=[Depends(require_auth)])
    def tts_add_voice(body: dict):
        meta, data = tts_call(tts_gen.new_user_voice, body)
        return {'id': service.store.put_tts_voice(meta, data)} | meta

    @app.delete('/v1/tts/voices/{voice_id}', dependencies=[Depends(require_auth)])
    def tts_delete_voice(voice_id: str): return service.store.delete_tts_voice(voice_id)

    @app.get('/v1/tts/voices/{voice_id}/demo', dependencies=[Depends(require_auth)])
    def tts_demo(voice_id: str):
        path = tts_gen.demo_path(voice_id)
        data = path.read_bytes() if path else service.store.get_tts_voice(voice_id)[1]
        return Response(data, media_type='audio/wav', headers={'Cache-Control': 'private, max-age=86400'})

    @app.post('/v1/tts/speak', status_code=202, dependencies=[Depends(require_auth)])
    def tts_speak(body: dict): return tts_call(tts_jobs().submit, body)

    @app.get('/v1/tts/jobs/{job_id}', dependencies=[Depends(require_auth)])
    def tts_job(job_id: str): return tts_call(tts_jobs().view, job_id)

    @app.get('/v1/tts/jobs/{job_id}/audio', dependencies=[Depends(require_auth)])
    def tts_audio(job_id: str):
        return Response(tts_call(tts_jobs().audio_of, job_id), media_type='audio/wav', headers={'Cache-Control': 'private, max-age=3600'})

    @app.post('/v1/tts/unload', dependencies=[Depends(require_auth)])
    def tts_unload(): return tts_call(tts_gen.unload)

    @app.get('/v1/errors', dependencies=[Depends(require_auth)])
    def errors():
        """Human texts for error codes, e.g. a generation's error_code (stage 4b)."""
        return {'errors': ERROR_CATALOG}

    @app.get('/v1/presets', dependencies=[Depends(require_auth)])
    def presets():
        from server.ego_core.presets import PRESETS, MEASUREMENTS, SAMPLING_FIELDS
        return {'presets': [{'id': p['id'], 'name': p['name'][0], 'description': p['description'][0],
                             'sampling': {k: p['values'][k] for k in SAMPLING_FIELDS},
                             'measured': MEASUREMENTS.get(p['id'])} for p in PRESETS]}

    @app.get('/v1/chats/{chat_id}/settings', dependencies=[Depends(require_auth)])
    def chat_settings(chat_id: str): return service.store.chat_settings(chat_id)

    @app.put('/v1/chats/{chat_id}/settings', dependencies=[Depends(require_auth)])
    def put_chat_settings(chat_id: str, body: ChatSettingsCommand):
        if body.preset_id is not None:
            from server.ego_core.presets import preset
            try: preset(body.preset_id)
            except ValueError: raise CoreFailure('NOT_FOUND') from None
        return service.store.set_chat_settings(chat_id, body.preset_id)

    # ---- owner profile (2026-11-06): one person per installation -----------------------------
    def need_profile():
        profile = getattr(service, 'profile', None)
        if profile is None:
            raise CoreFailure('MEMORY_UNAVAILABLE')
        return profile

    @app.get('/v1/profile', dependencies=[Depends(require_auth)])
    def get_profile():
        return need_profile().get()

    @app.put('/v1/profile', dependencies=[Depends(require_auth)])
    def put_profile(body: ProfileCommand):
        return need_profile().set(body.profile)

    @app.get('/v1/profile/avatar', dependencies=[Depends(require_auth)])
    def get_profile_avatar():
        data, media_type = need_profile().avatar()
        return Response(data, media_type=media_type, headers={'Cache-Control': 'no-store'})

    @app.put('/v1/profile/avatar', dependencies=[Depends(require_auth)])
    async def put_profile_avatar(request: Request):
        data = await request.body()
        return need_profile().set_avatar(data, request.headers.get('content-type', ''))

    @app.delete('/v1/profile/avatar', dependencies=[Depends(require_auth)])
    def delete_profile_avatar():
        return need_profile().delete_avatar()

    # ---- chat list with metadata, deletion (2026-11-06) --------------------------------
    @app.get('/v1/chats/library', dependencies=[Depends(require_auth)])
    def chat_library():
        return {'chats': service.store.chats_with_meta()}

    @app.put('/v1/chats/{chat_id}/meta', dependencies=[Depends(require_auth)])
    def put_chat_meta(chat_id: str, body: ChatMetaCommand):
        if not _UUID.fullmatch(chat_id):
            raise CoreFailure('INVALID_ID')
        return service.store.set_chat_meta(chat_id, body.meta, title=body.title)

    @app.delete('/v1/chats/{chat_id}', dependencies=[Depends(require_auth)])
    def delete_chat(chat_id: str):
        if not _UUID.fullmatch(chat_id):
            raise CoreFailure('INVALID_ID')
        return service.delete_chat(chat_id)

    # ---- chat memory (stage 4h) -----------------------------------------------------
    def memory_of(chat_id, item_id=None):
        if not _UUID.fullmatch(chat_id) or (item_id is not None and not _UUID.fullmatch(item_id)):
            raise CoreFailure('INVALID_ID')
        memory = getattr(service, 'memory', None)
        if memory is None:
            raise CoreFailure('MEMORY_UNAVAILABLE')
        return memory

    @app.get('/v1/chats/{chat_id}/memory', dependencies=[Depends(require_auth)])
    def chat_memory(chat_id: str):
        return memory_of(chat_id).view(chat_id)

    @app.put('/v1/chats/{chat_id}/memory/settings', dependencies=[Depends(require_auth)])
    def chat_memory_settings(chat_id: str, body: MemorySettingsCommand):
        return memory_of(chat_id).settings(chat_id, enabled=body.enabled, interval=body.interval)

    @app.post('/v1/chats/{chat_id}/memory', dependencies=[Depends(require_auth)])
    def add_chat_memory(chat_id: str, body: MemoryItemCommand):
        return memory_of(chat_id).add(chat_id, body.text, pinned=body.pinned)

    @app.put('/v1/chats/{chat_id}/memory/{item_id}', dependencies=[Depends(require_auth)])
    def edit_chat_memory(chat_id: str, item_id: str, body: MemoryEditCommand):
        return memory_of(chat_id, item_id).update(chat_id, item_id, text=body.text, pinned=body.pinned)

    @app.delete('/v1/chats/{chat_id}/memory/{item_id}', dependencies=[Depends(require_auth)])
    def delete_chat_memory(chat_id: str, item_id: str):
        return memory_of(chat_id, item_id).delete(chat_id, item_id)

    @app.post('/v1/chats/{chat_id}/memory/extract', status_code=202, dependencies=[Depends(require_auth)])
    def extract_chat_memory(chat_id: str):
        memory_of(chat_id)
        return service.extract_memory_now(chat_id)

    @app.post('/v1/enchant', dependencies=[Depends(require_auth)])
    def enchant(body: EnchantCommand):
        if len(body.context) > 20 or any(len(v) > 6000 for v in body.context.values()):
            raise CoreFailure('INVALID_INPUT')
        return service.enchant(body.kind, body.field, body.text, body.context)

    @app.get('/v1/worlds', dependencies=[Depends(require_auth)])
    def worlds(): return {'worlds': service.store.list_records('world')}

    @app.put('/v1/worlds/{world_id}', dependencies=[Depends(require_auth)])
    def put_world(world_id: str, body: RecordCommand):
        return service.store.put_record('world', world_id, body.data, body.expected_version)

    @app.delete('/v1/worlds/{world_id}', dependencies=[Depends(require_auth)])
    def delete_world(world_id: str, expected_version: int | None = Query(default=None, ge=0)):
        return service.store.delete_record('world', world_id, expected_version)

    @app.post('/v1/client/launch', dependencies=[Depends(require_auth)])
    def client_launch():
        """Admin-only: mint a single-use code for opening the EGO client."""
        code = secrets.token_urlsafe(32)
        now = time.monotonic()
        with client_lock:
            for stale in [k for k, expiry in launch_codes.items() if expiry <= now]:
                del launch_codes[stale]
            if len(launch_codes) >= 16:
                raise CoreFailure('CORE_BUSY')
            launch_codes[code] = now + CLIENT_CODE_TTL
        return {'code': code, 'expires_in': CLIENT_CODE_TTL, 'path': '/app/' if client_dir else None}

    @app.get('/v1/network', dependencies=[Depends(require_auth)])
    def network_status():
        """Home-network access as Core started it (no secrets)."""
        from server.ego_core.lan import urls
        status = network() if network is not None else {'enabled': False}
        return {'enabled': bool(status.get('enabled')), 'port': status.get('port'), 'urls': urls(status),
                'fingerprint': status.get('fingerprint'), 'error': status.get('error')}

    # ---- 2026-11-06: tunnel for access from outside the home network ------------------------
    # Only Control Center on this PC (admin token, loopback) can see or switch it.
    def need_tunnel(request: Request):
        if tunnel is None:
            raise CoreFailure('TUNNEL_UNAVAILABLE')
        if not _is_local(request):
            raise CoreFailure('UNAUTHORIZED')
        return tunnel

    @app.get('/v1/tunnel', dependencies=[Depends(require_auth)], include_in_schema=False)
    def tunnel_status(request: Request):
        return need_tunnel(request).status()

    @app.post('/v1/tunnel/start', dependencies=[Depends(require_auth)], include_in_schema=False)
    def tunnel_start(request: Request):
        return need_tunnel(request).start()

    @app.post('/v1/tunnel/stop', dependencies=[Depends(require_auth)], include_in_schema=False)
    def tunnel_stop(request: Request):
        return need_tunnel(request).stop()

    @app.post('/v1/client/session')
    def client_session(body: ClientSessionCommand, request: Request):
        """Exchange a single-use launch code for a client bearer token (this PC only)."""
        if not _is_local(request):
            raise CoreFailure('UNAUTHORIZED')
        with client_lock:
            expiry = launch_codes.pop(body.code, None)
            if expiry is None or expiry <= time.monotonic():
                raise CoreFailure('UNAUTHORIZED')
            if len(client_tokens) >= 64:
                client_tokens.pop()
            token = secrets.token_urlsafe(32)
            client_tokens.add(token)
        return {'token': token, 'scope': 'client'}

    # ---- accounts (stage 4g): sign-in for devices other than this PC -------------
    def need_accounts(throttle=False):
        if accounts is None:
            raise CoreFailure('ACCOUNTS_UNAVAILABLE')
        if throttle:
            accounts.throttle()
        return accounts

    def who(request):
        return {'device': _device(request), 'address': request.client.host if request.client else ''}

    @app.get('/v1/account/state')
    def account_state(request: Request):
        return need_accounts().state()

    @app.post('/v1/account/login')
    def account_login(body: AccountCredentials, request: Request):
        return need_accounts(True).login(body.email, body.password, **who(request))

    @app.get('/v1/account/me', dependencies=[Depends(require_auth)])
    def account_me(request: Request):
        return need_accounts().me(getattr(request.state, 'account_token', None))

    @app.put('/v1/account/profile', dependencies=[Depends(require_auth)])
    def account_profile(body: AccountProfileCommand, request: Request):
        return need_accounts().set_profile(body.profile, getattr(request.state, 'account_token', None))

    # Owner-only (admin token, this PC): accounts that may sign in from other devices.
    @app.get('/v1/accounts/users', dependencies=[Depends(require_auth)], include_in_schema=False)
    def accounts_list():
        return need_accounts().list_users()

    @app.post('/v1/accounts/users', status_code=201, dependencies=[Depends(require_auth)], include_in_schema=False)
    def accounts_add(body: AccountNewUser):
        return need_accounts().add_user(body.email, body.password)

    @app.post('/v1/accounts/users/remove', dependencies=[Depends(require_auth)], include_in_schema=False)
    def accounts_remove(body: AccountRemoveUser):
        return need_accounts().remove_user(body.email)

    @app.post('/v1/accounts/users/password', dependencies=[Depends(require_auth)], include_in_schema=False)
    def accounts_password(body: AccountNewUser):
        return need_accounts().set_user_password(body.email, body.password)

    @app.post('/v1/account/logout', dependencies=[Depends(require_auth)])
    def account_logout(request: Request):
        token = getattr(request.state, 'account_token', None)
        if token is None:
            raise CoreFailure('INVALID_INPUT')       # the PC has no account session to end
        return need_accounts().logout(token, **who(request))

    @app.post('/v1/account/logout-all', dependencies=[Depends(require_auth)])
    def account_logout_all(request: Request):
        token = getattr(request.state, 'account_token', None)
        if token is None:
            return need_accounts().logout_everywhere_local()
        return need_accounts().logout(token, everywhere=True, **who(request))

    if client_dir is not None:
        # The client is static, secret-free HTML/JS served from the same
        # loopback origin, so the browser needs no CORS or file:// storage.
        app.mount('/app', StaticFiles(directory=str(client_dir), html=True), name='client')

        # 2026-11-06: an address typed by hand without /app/ (e.g. on a phone) opens the client.
        @app.get('/', include_in_schema=False)
        def root():
            return Response(status_code=307, headers={'Location': '/app/'})

    @app.post('/v1/system/shutdown', dependencies=[Depends(require_auth)])
    def shutdown(body: Command):
        receipt = service.store.record_control(body.operation_id, 'shutdown')
        return JSONResponse(receipt, background=BackgroundTask(shutdown_callback) if shutdown_callback else None)

    return app
