"""Windows user-session core lifecycle. No plaintext key or TLS bypass mode."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
import httpx
import uvicorn
from runtime.adapters.llama_cpp import LlamaRuntime
from server.ego_core.api import create_app
from server.ego_core.config import CoreConfig, linked, load_config, save_config
from server.ego_core.keys import KeyFailure, KeyVault
from server.ego_core.service import CoreService
from server.ego_core.store import MainStore, ProfileStore

FROZEN = bool(getattr(sys, 'frozen', False))
CORE_EXE = 'EGO-Core.exe'  # Console sibling of EGO.exe: hidden Core, CLI, force-close.
# Source checkout: the project folder. Packaged EGO.exe: the folder holding the
# exe, so data/, settings/ and logs/ live next to it (never inside the bundle).
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[2]


def daemon_command(root):
    """Hidden Core process: the same EGO.exe in daemon mode, or python -m when run from source."""
    head = [str(Path(sys.executable).with_name(CORE_EXE)), 'daemon'] if FROZEN else [sys.executable, '-m', 'server.ego_core.daemon']
    return head + ['--root', str(root), '--startup-control']


def _port_accepting(port, timeout=.05):
    """True once a listener accepts on loopback. A refused connect on Windows
    would block ~2 s; a short timeout turns it into a cheap 50 ms poll."""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False
STOP_TIMEOUT = 20.
START_TIMEOUT = 12.
CHILD_CLEANUP_TIMEOUT = 20.
_owned_job_handle = None


def installation_id(root):
    """A copied data directory cannot authorize a different application root."""
    canonical = os.path.normcase(str(Path(root).resolve()))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def startup_control_event(stream):
    """A private startup pipe accepts STOP; EOF leaves the Core independent."""
    event = threading.Event()
    def read():
        try:
            if stream.readline(16).strip() == 'STOP':
                event.set()
        except (OSError, ValueError):
            pass
    threading.Thread(target=read, daemon=True, name='ego-startup-control').start()
    return event


def own_process_tree():
    """Before spawning llama, retain an exact kill-on-close Windows job.

    Linux startup already receives its own session from Popen. The Windows
    handle deliberately stays open until process exit, including after the
    launching control client has disconnected. No process inventory is used.
    """
    global _owned_job_handle
    if os.name != 'nt' or _owned_job_handle is not None:
        return
    import ctypes
    from ctypes import wintypes
    class BasicLimit(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                    ('PerJobUserTimeLimit', ctypes.c_longlong), ('LimitFlags', wintypes.DWORD),
                    ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                    ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t),
                    ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
    class ExtendedLimit(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BasicLimit), ('IoInfo', IOCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ControlFailure('CORE_PROCESS_OWNERSHIP_FAILED')
    limits = ExtendedLimit()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())):
        kernel.CloseHandle(handle)
        raise ControlFailure('CORE_PROCESS_OWNERSHIP_FAILED')
    _owned_job_handle = handle


def _cleanup_started_child(process):
    """Stop only this call's Core and its explicitly owned process tree."""
    try:
        if process.stdin is not None:
            try:
                process.stdin.write(b'STOP\n')
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
        try:
            process.wait(timeout=CHILD_CLEANUP_TIMEOUT)
            return
        except subprocess.TimeoutExpired:
            pass
        if os.name == 'nt':
            # --startup-control installs the job before runtime assembly.
            # Terminating this exact Core closes its job and all owned children.
            process.kill()
        else:
            # Popen created this session; native llama inherits its process group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        raise ControlFailure('CORE_START_CLEANUP_TIMEOUT') from None


class ControlFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def child_failure(path):
    if linked(path) or not path.is_file() or path.stat().st_size>8192:
        return {}
    try:
        result=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(result,dict): return {}
        safe={}
        code=result.get('code')
        if isinstance(code,str) and re.fullmatch('[A-Z][A-Z0-9_]{0,63}',code): safe['cause_code']=code
        for name in ('errno','winerror'):
            value=result.get(name)
            if type(value) is int and -2147483648<=value<=4294967295: safe[name]=value
        return safe
    except (OSError,ValueError): return {}


def log_directory(root):
    path = root/'logs'
    if linked(path): raise ControlFailure('LOGS_PATH_UNSAFE')
    path.mkdir(exist_ok=True)
    return path


class APIErrorSink:
    """Bounded diagnostic codes only; never accepts request/exception text."""
    def __init__(self, root):
        self.logs=log_directory(root)
        self.paths=[self.logs/name for name in ('core-api-errors.log','core-api-errors.log.1','core-api-errors.log.2','latest-core-api-error.json')]
        self._check()
        self.handler=RotatingFileHandler(self.paths[0],maxBytes=262144,backupCount=2,encoding='utf-8',delay=True)
    def _check(self):
        if any(linked(path) for path in self.paths): raise ControlFailure('LOGS_PATH_UNSAFE')
    def __call__(self, code, *, diagnostics=None):
        if not isinstance(code,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',code):
            raise ControlFailure('INVALID_DIAGNOSTIC_CODE')
        self._check()
        payload={'status':'FAIL','code':code,'timestamp':datetime.now(timezone.utc).isoformat()}
        if isinstance(diagnostics,dict):
            for key in ('errno','winerror','exit_code'):
                if type(diagnostics.get(key)) is int: payload[key]=diagnostics[key]
            if diagnostics.get('state')=='ERROR': payload['runtime_state']='ERROR'
        encoded=json.dumps(payload)
        self.handler.handle(logging.LogRecord('ego',logging.ERROR,'',0,encoded,(),None))
        self.paths[-1].write_text(encoded+'\n',encoding='utf-8')
    def close(self): self.handler.close()


@contextmanager
def CoreLease(directory):
    """OS-owned lock survives stale files and releases on process death."""
    path=directory/'core.lock'
    if linked(directory) or linked(path): raise ControlFailure('DATA_PATH_UNSAFE')
    fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0),0o600)
    locked=False
    try:
        if os.fstat(fd).st_size==0:
            os.write(fd,b'0')
        os.lseek(fd,0,os.SEEK_SET)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError:
            raise ControlFailure('CORE_ALREADY_RUNNING') from None
        yield
    finally:
        if locked:
            if os.name=='nt':
                import msvcrt
                os.lseek(fd,0,os.SEEK_SET); msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)


def initialize(root, *, vault_factory=KeyVault):
    data = root/'data'
    vault = vault_factory(data)
    identity = vault.unlock() if (data/KeyVault.VAULT).exists() else vault.initialize()
    main = MainStore(data/'main.db', identity.secrets.main_key)
    try:
        profile = ProfileStore(data/'profile.db', identity.secrets.profile_key)
        profile.close()
    finally:
        main.close()
    path = data/'config.json'
    if not path.exists(): save_config(path, CoreConfig())
    else: load_config(path)
    return {'status':'PASS','code':'CORE_INITIALIZED','recovery':'M1 vault is user-bound; keep original data and Windows profile. Portable application backup is not implemented.'}


@dataclass
class AssembledCore:
    app: object
    service: CoreService
    identity: object
    config: CoreConfig
    def close(self):
        self.service.close()
        self.service.store.close()


def assemble(root, *, vault_factory=KeyVault, runtime_factory=LlamaRuntime):
    data = root/'data'
    identity = vault_factory(data).unlock()
    config = load_config(data/'config.json')
    store = MainStore(data/'main.db',identity.secrets.main_key)
    service = None
    try:
        profile = ProfileStore(data/'profile.db',identity.secrets.profile_key)
        profile.close()
        runtime_config = config.runtime_config()
        service = CoreService(store,runtime=runtime_factory(runtime_config) if runtime_config else None,
                              runtime_factory=runtime_factory)
        service.installation_id = installation_id(root)
        app = create_app(service,identity.secrets.admin_token)
        service.start_runtime()
        return AssembledCore(app,service,identity,config)
    except BaseException:
        if service is not None:
            service.close()
        store.close()
        raise


def parse_generation_sse(lines, *, with_cursor=False):
    """Only accepted generation snapshots; ignore heartbeats and unknown events."""
    event_type=None
    data=[]
    cursor=None
    for line in lines:
        if not line:
            if event_type=='generation' and data:
                raw='\n'.join(data)
                if len(raw)>1048576: raise ValueError('INVALID_GENERATION_EVENT')
                try: payload=json.loads(raw)
                except ValueError: raise ValueError('INVALID_GENERATION_EVENT') from None
                if not isinstance(payload,dict) or payload.get('state') not in {'queued','started','streaming','completed','stopped','failed'}:
                    raise ValueError('INVALID_GENERATION_EVENT')
                yield (cursor, payload) if with_cursor else payload
            event_type=None
            data=[]
            cursor=None
        elif line.startswith('id: '):
            candidate=line[4:]
            if not re.fullmatch(r'[a-f0-9]{64}',candidate):
                if with_cursor: raise ValueError('INVALID_EVENT_CURSOR')
            else: cursor=candidate
        elif line.startswith('event: '):
            event_type=line[7:]
        elif line.startswith('data: '):
            data.append(line[6:])
            if sum(map(len,data))>1048576: raise ValueError('INVALID_GENERATION_EVENT')


def generation_event_stream(root, generation_id, *, timeout=25, vault_factory=KeyVault, last_event_id=None, with_cursor=False):
    """Authorized SSE snapshots. Caller may fall back to GET after disconnect."""
    from uuid import UUID
    try:
        if UUID(generation_id).version!=4: raise ValueError
    except (TypeError,ValueError):
        raise ControlFailure('INVALID_GENERATION_ID') from None
    if last_event_id is not None and not re.fullmatch(r'[a-f0-9]{64}',last_event_id):
        raise ControlFailure('INVALID_EVENT_CURSOR')
    request(root,'GET','/v1/system/status',vault_factory=vault_factory,timeout=5)
    identity=vault_factory(root/'data').unlock()
    config=load_config(root/'data/config.json')
    verify=ssl.create_default_context(cafile=str(identity.certfile)) if not config.local_http else True
    scheme='http' if config.local_http else 'https'
    url=f'{scheme}://127.0.0.1:{config.api_port}/v1/generations/{generation_id}/events'
    headers={'Authorization':'Bearer '+identity.secrets.admin_token,
             'X-EGO-Installation':installation_id(root)}
    if last_event_id is not None:
        headers['Last-Event-ID']=last_event_id
    try:
        with httpx.Client(verify=verify,trust_env=False,timeout=httpx.Timeout(timeout,connect=3),follow_redirects=False) as client:
            with client.stream('GET',url,headers=headers) as response:
                if response.status_code==401: raise ControlFailure('CORE_AUTH_FAILED')
                if response.status_code==403: raise ControlFailure('CORE_INSTALLATION_MISMATCH')
                if response.status_code!=200: raise ControlFailure('CORE_STREAM_REJECTED')
                for item in parse_generation_sse(response.iter_lines(), with_cursor=with_cursor):
                    snapshot=item[1] if with_cursor else item
                    if snapshot.get('id')!=generation_id: raise ControlFailure('CORE_RESPONSE_INVALID')
                    yield item
    except (httpx.HTTPError, ValueError):
        raise ControlFailure('CORE_STREAM_INTERRUPTED') from None


def request(root, method, path, *, payload=None, vault_factory=KeyVault, timeout=5):
    if path != '/v1/system/status':
        # Older Cores ignore the installation header. Prove ownership before
        # any data request or mutation, including shutdown of a copied vault.
        request(root,'GET','/v1/system/status',vault_factory=vault_factory,timeout=timeout)
    identity = vault_factory(root/'data').unlock()
    config = load_config(root/'data/config.json')
    context = ssl.create_default_context(cafile=str(identity.certfile)) if not config.local_http else True
    protocol = 'http' if config.local_http else 'https'
    try:
        with httpx.Client(verify=context,trust_env=False,timeout=timeout,follow_redirects=False) as client:
            response = client.request(method,f'{protocol}://127.0.0.1:{config.api_port}'+path,
                headers={'Authorization':'Bearer '+identity.secrets.admin_token,
                         'X-EGO-Installation':installation_id(root)},json=payload)
    except httpx.ConnectError as exc:
        # Only a fixed TLS classification is retained, never the raw message.
        code = 'CORE_TLS_FAILED' if 'CERTIFICATE_VERIFY_FAILED' in str(exc) else 'CORE_OFFLINE'
        raise ControlFailure(code) from None
    except httpx.ConnectTimeout:
        # Windows retries a refused loopback SYN for ~2 s, so short status
        # probes time out instead of failing. A loopback listener accepts in
        # the kernel even when the app is busy: no connection means offline.
        raise ControlFailure('CORE_OFFLINE') from None
    except httpx.HTTPError:
        raise ControlFailure('CORE_NETWORK_ERROR') from None
    if response.status_code == 401: raise ControlFailure('CORE_AUTH_FAILED')
    if response.status_code == 403: raise ControlFailure('CORE_INSTALLATION_MISMATCH')
    if not 200 <= response.status_code < 300:
        # Preserve fixed actionable API errors, never arbitrary response text.
        if len(response.content)<=8192:
            try: code=response.json().get('code')
            except (ValueError,AttributeError): code=None
            # Core emits only fixed operational codes (never user text), so pass
            # any well-formed code through; callers can then show the reason
            # (VERSION_CONFLICT, GENERATION_BUSY, NOTHING_TO_REGENERATE, ...).
            if isinstance(code,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',code) and code not in {'UNAUTHORIZED'}:
                raise ControlFailure(code)
        raise ControlFailure('CORE_REQUEST_FAILED')
    if len(response.content)>1048576: raise ControlFailure('CORE_RESPONSE_INVALID')
    try: result = response.json()
    except ValueError: raise ControlFailure('CORE_RESPONSE_INVALID') from None
    if path == '/v1/system/status' and (
            not isinstance(result,dict) or result.get('installation_id') != installation_id(root)):
        raise ControlFailure('CORE_INSTALLATION_MISMATCH')
    return result


def start(root, *, cancel=None, progress=None):
    if cancel is not None and cancel.is_set():
        return {'status':'CANCELLED','code':'CORE_START_CANCELLED'}
    # Verify the vault/config before spawning. A foreign endpoint is never
    # accepted merely for returning an HTTP health status.
    KeyVault(root/'data').unlock()
    config = load_config(root/'data/config.json')
    # The data lease is held by any Core serving this installation. If it is
    # free, nothing to find over HTTP: skip the probe (a refused loopback
    # connect costs ~2.3 s on Windows). If held, ask that Core as before.
    if not _lease_available(root):
        try:
            status = request(root,'GET','/v1/system/status')
            return {'status':'PASS','code':'CORE_ALREADY_RUNNING','core':status}
        except ControlFailure as exc:
            if exc.code!='CORE_OFFLINE': raise
    from server.ego_core import preflight, logrotate
    try: logrotate.rotate(root)  # Stage 4e: bounded logs/ without user action.
    except OSError: pass
    check = preflight.run(root, check_gpu=False)  # nvidia-smi is too slow for every start
    if not check['ok']:
        failure = ControlFailure(check['errors'][0]['code'])
        failure.diagnostics = {'preflight': check}
        raise failure
    log = log_directory(root)/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')+'-core-server.log')
    with log.open('xb') as output:
        process = subprocess.Popen(daemon_command(root),
            cwd=ROOT,stdin=subprocess.PIPE,stdout=output,stderr=output,
            creationflags=(subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP) if os.name=='nt' else 0,
            start_new_session=os.name!='nt')
    deadline = time.monotonic()+START_TIMEOUT
    heartbeat = 0.
    while time.monotonic()<deadline:
        if cancel is not None and cancel.is_set():
            _cleanup_started_child(process)
            return {'status':'CANCELLED','code':'CORE_START_CANCELLED'}
        if progress is not None and time.monotonic()>=heartbeat:
            progress({'stage':'STARTING_RUNTIME','message':'Waiting for local Core readiness.',
                      'started_at':datetime.now(timezone.utc).isoformat()})
            heartbeat=time.monotonic()+1
        if process.poll() is not None:
            if process.stdin is not None: process.stdin.close()
            failure=ControlFailure('CORE_START_FAILED')
            failure.diagnostics=child_failure(log)
            raise failure
        port = getattr(config, 'api_port', None)
        if port is not None and not _port_accepting(port):
            time.sleep(.05)
            continue
        try:
            status = request(root,'GET','/v1/system/status',timeout=3)
            if cancel is not None and cancel.is_set():
                _cleanup_started_child(process)
                return {'status':'CANCELLED','code':'CORE_START_CANCELLED'}
            if process.stdin is not None: process.stdin.close()
            return {'status':'PASS','code':'CORE_STARTED','core':status,'preflight':check['warnings']}
        except ControlFailure as exc:
            if exc.code not in {'CORE_OFFLINE','CORE_NETWORK_ERROR'}:
                # Terminate only the child this call created, never a stored PID.
                _cleanup_started_child(process)
                raise
        time.sleep(.05)
    _cleanup_started_child(process)
    raise ControlFailure('CORE_START_TIMEOUT')


def _lease_available(root):
    # Without a data folder no Core of this installation can hold the lease.
    if not (root/'data').exists():
        return True
    try:
        with CoreLease(root/'data'):
            return True
    except ControlFailure as exc:
        if exc.code == 'CORE_ALREADY_RUNNING':
            return False
        raise


def stop(root):
    if _lease_available(root):
        return {'status':'PASS','code':'CORE_ALREADY_STOPPED'}
    acknowledged = False
    # While uvicorn closes, an in-flight request can see a reset/partial
    # response (CORE_NETWORK_ERROR) instead of a refused connection. That is
    # an expected shutdown symptom, not a failure of stop().
    closing = {'CORE_OFFLINE','CORE_NETWORK_ERROR'}
    try:
        request(root,'POST','/v1/system/shutdown',payload={'operation_id':str(uuid.uuid4())},timeout=10)
        acknowledged = True
    except ControlFailure as exc:
        if exc.code not in closing: raise
        acknowledged = exc.code=='CORE_NETWORK_ERROR'
    deadline = time.monotonic()+STOP_TIMEOUT
    while time.monotonic()<deadline:
        # Core releases the data lease only after closing SQLCipher and its
        # runtime; that is the completion signal, not the listener closing.
        if _lease_available(root):
            return {'status':'PASS','code':'CORE_STOPPED' if acknowledged else 'CORE_ALREADY_STOPPED'}
        time.sleep(.05)
    raise ControlFailure('CORE_STOP_TIMEOUT')


def configure(root):
    old = load_config(root/'data/config.json')
    binary = input('Local llama-server.exe path (blank keeps current/unconfigured): ').strip().strip('"')
    if not binary:
        return {'status':'PASS','code':'CONFIGURATION_UNCHANGED'}
    model = input('Local GGUF model path: ').strip().strip('"')
    if not model: raise ControlFailure('MODEL_NOT_PROVIDED')
    try:
        request(root,'GET','/v1/system/status')
    except ControlFailure as exc:
        if exc.code!='CORE_OFFLINE': raise
    else: raise ControlFailure('STOP_CORE_BEFORE_CONFIGURE')
    values=old.model_dump(); values.update(binary=str(Path(binary).absolute()),model=str(Path(model).absolute()))
    config=CoreConfig(**values)
    LlamaRuntime(config.runtime_config()).validate()
    save_config(root/'data/config.json',config)
    return {'status':'PASS','code':'MODEL_CONFIGURED','downloads':False,'gpu_execution':'NOT_VERIFIED'}


def smoke(root):
    state=request(root,'GET','/v1/system/status')
    if state['runtime']=='unconfigured':
        return {'status':'BLOCKED','code':'MODEL_NOT_PROVIDED','instruction':'Run 10_CORE_SETUP.bat and select local runtime/model files.'}
    chat=request(root,'POST','/v1/chats',payload={'operation_id':str(uuid.uuid4()),'title':'EGO M1 diagnostic smoke'})
    gen=request(root,'POST','/v1/generations',payload={'operation_id':str(uuid.uuid4()),'chat_id':chat['id'],
        'content':'Reply with a short confirmation that local generation is available.','expected_version':chat['version']})['generation']
    deadline=time.monotonic()+load_config(root/'data/config.json').startup_timeout+90
    while time.monotonic()<deadline:
        result=request(root,'GET','/v1/generations/'+gen['id'])
        if result['state'] in {'completed','stopped','failed'}:
            return {'status':'PASS' if result['state']=='completed' else 'FAIL',
                'code':'CORE_GENERATION_SMOKE' if result['state']=='completed' else result['error_code'] or 'GENERATION_STOPPED',
                'generation_id':gen['id'],'output_characters':len(result['text']),
                'gpu_execution':'NOT_VERIFIED','synthetic_chat_retained':True}
        time.sleep(.2)
    raise ControlFailure('SMOKE_TIMEOUT_RESULT_UNKNOWN')


def client_directory(root):
    """EGO client served at /app: bundled <root>/client, else a sibling EGO-Client checkout."""
    for candidate in (root/'client', root.parent/'EGO-Client'):
        if (candidate/'index.html').is_file() and not linked(candidate):
            return candidate
    return None


def client_url(root):
    """Start Core if needed and return a one-time client URL (code in the fragment, never logged)."""
    if _lease_available(root):
        start(root)  # No Core of this installation is running; skip the slow probe.
    else:
        try:
            request(root,'GET','/v1/system/status',timeout=5)
        except ControlFailure as exc:
            if exc.code!='CORE_OFFLINE': raise
            start(root)
    launch=request(root,'POST','/v1/client/launch',payload=None,timeout=5)
    if not launch.get('path'):
        raise ControlFailure('CLIENT_NOT_INSTALLED')
    config=load_config(root/'data/config.json')
    scheme='http' if config.local_http else 'https'
    return f"{scheme}://127.0.0.1:{config.api_port}{launch['path']}#code={launch['code']}"


def open_client(root):
    import webbrowser
    url=client_url(root)
    if not webbrowser.open(url):
        raise ControlFailure('BROWSER_UNAVAILABLE')
    # The URL carries a single-use secret; report success without it.
    return {'status':'PASS','code':'CLIENT_OPENED'}


def _start_lan(root, core, app, network):
    """Stage 4i: optional HTTPS listener on this PC's home-network addresses.
    Never fatal for Core: problems are reported in data\\lan-status.json."""
    from server.ego_core import lan
    settings=lan.load_settings(root)
    network.clear(); network.update({'enabled':settings['lan_access'],'port':settings['lan_port'],'addresses':[],
                                     'fingerprint':None,'error':None})
    sockets=[]
    try:
        if not settings['lan_access']:
            return None,None,sockets
        addresses=lan.private_addresses()
        if not addresses:
            network['error']='LAN_NO_ADDRESS'
            return None,None,sockets
        certfile,keyfile,fingerprint=lan.ensure_certificate(core.identity.certfile.parent,
                                                            core.identity.secrets.tls_password,addresses)
        network['fingerprint']=fingerprint
        for address in addresses:
            sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            try:
                if os.name=='nt': sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                sock.bind((address,settings['lan_port'])); sock.listen(64)
            except OSError:
                sock.close(); continue
            sockets.append(sock); network['addresses'].append(address)
        if not sockets:
            network['error']='LAN_PORT_IN_USE'
            return None,None,sockets
        lan_server=uvicorn.Server(uvicorn.Config(app,port=settings['lan_port'],
            ssl_certfile=str(certfile),ssl_keyfile=str(keyfile),ssl_keyfile_password=core.identity.secrets.tls_password,
            ssl_version=ssl.PROTOCOL_TLS_SERVER,proxy_headers=False,server_header=False,
            log_config=None,log_level='critical',access_log=False,timeout_graceful_shutdown=3,limit_concurrency=32))
        thread=threading.Thread(target=lambda:lan_server.run(sockets=sockets),daemon=True,name='ego-lan-https')
        thread.start()
        return lan_server,thread,sockets
    except Exception as exc:
        network['error']=getattr(exc,'code',None) or 'LAN_START_FAILED'
        for sock in sockets:
            try: sock.close()
            except OSError: pass
        return None,None,[]
    finally:
        try: lan.write_status(root,{**network,'urls':lan.urls(network)})
        except OSError: pass


def serve(root, *, vault_factory=KeyVault, runtime_factory=LlamaRuntime, startup_control=None):
    # Binding the actual socket once and passing it into uvicorn is atomic;
    # there is no preflight listener adoption or port rotation.
    logging.disable(logging.CRITICAL)
    startup_stop = startup_control_event(startup_control) if startup_control is not None else None
    if startup_stop is not None:
        own_process_tree()
    # Claim the data lease and actual listener before recovery mutates any
    # operation. A failed/duplicate launch must not fail another core's work.
    config=load_config(root/'data/config.json')
    with CoreLease(root/'data'):
        if startup_stop is not None and startup_stop.is_set():
            return
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
            if os.name=='nt': listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            else: listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(('127.0.0.1',config.api_port))
            listener.listen(128)
            core=assemble(root,vault_factory=vault_factory,runtime_factory=runtime_factory)
            audit=None
            try:
                audit=APIErrorSink(root)
                core.service.runtime_diagnostic_callback=lambda event:audit(event['code'],diagnostics=event)
                failed=core.service.status()
                if failed['runtime_state']=='ERROR' and failed['last_runtime_error']:
                    audit(failed['last_runtime_error']['code'],diagnostics={**failed['last_runtime_error'],'state':'ERROR'})
                tls_options = {} if core.config.local_http else dict(
                    ssl_certfile=str(core.identity.certfile),ssl_keyfile=str(core.identity.keyfile),
                    ssl_keyfile_password=core.identity.secrets.tls_password,ssl_version=ssl.PROTOCOL_TLS_SERVER)
                server=uvicorn.Server(uvicorn.Config(core.app,host='127.0.0.1',port=core.config.api_port,
                    **tls_options,
                    log_config=None,log_level='critical',access_log=False,timeout_graceful_shutdown=3,
                    limit_concurrency=32))
                def shutdown():
                    core.service.close()
                    server.should_exit=True
                if startup_stop is not None:
                    def stop_startup():
                        startup_stop.wait()
                        shutdown()
                    threading.Thread(target=stop_startup,daemon=True,name='ego-startup-stop').start()
                from server.ego_core.accounts import Accounts
                accounts=Accounts(core.service.store)
                network={'enabled':False}
                from server.ego_core.tunnel import Tunnel
                tunnel=Tunnel(root)     # off until the owner turns it on in Control Center
                server.config.app=create_app(core.service,core.identity.secrets.admin_token,shutdown_callback=shutdown,
                                             error_callback=audit,client_dir=client_directory(root),accounts=accounts,
                                             network=lambda:dict(network),tunnel=tunnel)
                tunnel.app=server.config.app
                # The LAN listener shares the app and stops right after the main server.
                lan_server,lan_thread,lan_sockets=_start_lan(root,core,server.config.app,network)
                try:
                    server.run(sockets=[listener])
                finally:
                    try: tunnel.stop()      # never leave the house open after Core stops
                    except Exception: pass
                    if lan_server is not None:
                        lan_server.should_exit=True
                        lan_thread.join(5)
                    for sock in lan_sockets:
                        try: sock.close()
                        except OSError: pass
                    # Never leave a stale "reachable at ..." after Core stops.
                    from server.ego_core import lan
                    try: lan.write_status(root,{'enabled':False,'port':network.get('port'),'addresses':[],
                                                'fingerprint':network.get('fingerprint'),'error':None,'urls':[]})
                    except OSError: pass
            finally:
                try: core.close()
                finally:
                    if audit is not None: audit.close()



def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['setup','start','status','stop','smoke','configure','open-client','check'])
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args(argv)
    root=args.root.absolute()
    if not root.is_dir():
        print(json.dumps({'status':'FAIL','code':'PROJECT_ROOT_MISSING'})); return 2
    try:
        logs=log_directory(root)
        log=logs/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')+'-core-control.log')
        if linked(log): raise ControlFailure('LOGS_PATH_UNSAFE')
        result=None
        try:
            if args.action=='setup':
                result=initialize(root)
                if sys.stdin.isatty():
                    print('No portable application backup yet. Preserve the data folder and Windows user profile; losing DPAPI access can lock this M1 development store.',flush=True)
                    configured=configure(root)
                    result['configuration']=configured
            elif args.action=='configure': result=configure(root)
            elif args.action=='start': result=start(root)
            elif args.action=='stop': result=stop(root)
            elif args.action=='smoke': result=smoke(root)
            elif args.action=='open-client': result=open_client(root)
            elif args.action=='check':
                from server.ego_core import preflight
                report=preflight.run(root,core_running=(root/'data').is_dir() and not _lease_available(root))
                result={'status':'PASS' if report['ok'] and not report['warnings'] else ('WARN' if report['ok'] else 'FAIL'),
                        'code':'PREFLIGHT_OK' if report['ok'] else report['errors'][0]['code'],'preflight':report}
            elif (root/'data').is_dir() and _lease_available(root):
                result={'status':'FAIL','code':'CORE_OFFLINE'}  # Same answer, without a 2 s refused connect.
            else: result={'status':'PASS','code':'CORE_STATUS','core':request(root,'GET','/v1/system/status')}
        except Exception as exc:
            code=getattr(exc,'code','CONFIG_INVALID' if isinstance(exc,ValueError) else 'CORE_INTERNAL_ERROR')
            if not isinstance(code,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',code): code='CORE_INTERNAL_ERROR'
            result={'status':'FAIL','code':code}
            if isinstance(exc,ControlFailure): result.update(getattr(exc,'diagnostics',{}))
            if isinstance(exc,OSError): result.update(errno=exc.errno,winerror=getattr(exc,'winerror',None))
        encoded=json.dumps(result,ensure_ascii=True,indent=2)
        log.write_text(encoded+'\n',encoding='utf-8')
        latest=logs/'latest-core-report.json'
        if linked(latest): raise ControlFailure('LOGS_PATH_UNSAFE')
        latest.write_text(encoded+'\n',encoding='utf-8')
        print(encoded,flush=True)
        return 0 if result['status']=='PASS' else 2
    except Exception:
        print(json.dumps({'status':'FAIL','code':'CONTROL_LOGGING_FAILED'}),flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
