"""Small, synchronous llama-server adapter. No external service URLs or downloads."""
from __future__ import annotations

import errno
import json
import math
import os
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from socket import SHUT_RDWR, SHUT_WR
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import httpx


class RuntimeFailure(RuntimeError):
    def __init__(self, code: str, *, os_error: OSError | None = None,
                 operation: str | None = None):
        self.code = code
        self.os_error = os_error
        self.errno = getattr(os_error, 'errno', None)
        self.winerror = getattr(os_error, 'winerror', None)
        self.operation = operation
        super().__init__(code)


@dataclass(frozen=True)
class RuntimeConfig:
    binary: Path
    model: Path
    port: int = 18081
    context: int = 2048
    gpu_layers: int = 99
    startup_timeout: float = 120

    threads: int | None = None
    batch_size: int | None = None
    flash_attention: bool | None = None
    temperature: float = 0.
    top_p: float = 1.
    min_p: float = 0.
    top_k: int = 40
    repeat_penalty: float = 1.
    max_tokens: int = 128

    def __post_init__(self):
        if not 1024 <= self.port <= 65535 or not 256 <= self.context <= 32768:
            raise ValueError('Invalid port/context')
        if not 0 <= self.gpu_layers <= 999 or self.startup_timeout <= 0:
            raise ValueError('Invalid GPU layers/timeout')
        for value, upper in ((self.threads,1024),(self.batch_size,65536)):
            if value is not None and (type(value) is not int or not 1 <= value <= upper):
                raise ValueError('Invalid loading option')
        if self.flash_attention is not None and type(self.flash_attention) is not bool:
            raise ValueError('Invalid flash attention')
        if (not math.isfinite(self.temperature) or not 0 <= self.temperature <= 10
                or not math.isfinite(self.top_p) or not 0 < self.top_p <= 1
                or not math.isfinite(self.min_p) or not 0 <= self.min_p <= 1
                or type(self.top_k) is not int or not 0 <= self.top_k <= 100000
                or not math.isfinite(self.repeat_penalty) or not 0 < self.repeat_penalty <= 100
                or type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 4096):
            raise ValueError('Invalid generation option')


@dataclass(frozen=True)
class StreamEvent:
    content: str
    stop: bool = False
    tokens_predicted: int | None = None
    timings: dict | None = None
    finish_reason: str | None = None


class LlamaRuntime:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.process: subprocess.Popen | None = None
        self._generation = threading.Lock()
        self._starting = threading.Lock()
        self._startup_cancelled = threading.Event()
        self._lifecycle = threading.RLock()
        self._cancelled = threading.Event()
        self._http_lock = threading.Lock()
        self._http = {}
        self._completion_http = {}
        self._generation_active = False
        self._last_reaped_at = 0.0
        self.last_bind_diagnostics = {}
        self.last_bind_error = None
        self.transport_diagnostics = []
        self.url = f'http://127.0.0.1:{config.port}'

    def _binary_command(self) -> list[str]:
        return [str(self.config.binary.resolve())]

    def command(self) -> list[str]:
        command = self._binary_command() + [
            '--model', str(self.config.model.resolve()), '--host', '127.0.0.1',
            '--port', str(self.config.port), '--ctx-size', str(self.config.context),
            '--n-gpu-layers', str(self.config.gpu_layers), '--parallel', '1',
            '--no-webui', '--log-disable',
        ]
        if any(value is not None for value in
               (self.config.threads,self.config.batch_size,self.config.flash_attention)):
            from runtime.adapters.capabilities import probe_binary_cached
            capability = probe_binary_cached(self.config.binary)
            if capability['status'] != 'PASS':
                raise RuntimeFailure('UNSUPPORTED_RUNTIME_OPTION')
            for flag, value in (('--threads',self.config.threads),('--batch-size',self.config.batch_size)):
                if value is None:
                    continue
                option = capability['options'].get(flag,{})
                if not option.get('takes_value'):
                    raise RuntimeFailure('UNSUPPORTED_RUNTIME_OPTION')
                command.extend([flag,str(value)])
            if self.config.flash_attention is not None:
                option = capability['options'].get('--flash-attn')
                if option is None:
                    raise RuntimeFailure('UNSUPPORTED_RUNTIME_OPTION')
                value = 'on' if self.config.flash_attention else 'off'
                if value in option['values']:
                    command.extend(['--flash-attn',value])
                elif self.config.flash_attention and not option['takes_value']:
                    command.append('--flash-attn')
                else:
                    raise RuntimeFailure('UNSUPPORTED_RUNTIME_OPTION')
        return command

    @staticmethod
    def child_environment() -> dict[str, str]:
        # Allow only OS/runtime infrastructure; no inherited inference config,
        # proxy credentials, tracing/exporter settings or provider tokens.
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR',
                   'USERPROFILE', 'HOME', 'LOCALAPPDATA', 'APPDATA',
                   'CUDA_PATH', 'CUDA_VISIBLE_DEVICES', 'LD_LIBRARY_PATH'}
        return {k: v for k, v in os.environ.items() if k.upper() in allowed}

    def validate(self):
        if not self.config.model.is_file():
            raise RuntimeFailure('MODEL_NOT_PROVIDED')
        with self.config.model.open('rb') as f:
            if f.read(4) != b'GGUF':
                raise RuntimeFailure('INVALID_GGUF')
        if not self.config.binary.is_file():
            raise RuntimeFailure('RUNTIME_NOT_PROVIDED')

    @contextmanager
    def _client(self, timeout, *, completion=False):
        client = httpx.Client(trust_env=False, timeout=timeout)
        with self._http_lock:
            self._http[client] = None
            if completion:
                self._completion_http[client] = None
        try:
            yield client
        finally:
            self._close_client(client)

    def _response(self, client, response):
        with self._http_lock:
            if client in self._http:
                self._http[client] = response

    def _close_client(self, client):
        # Keep ownership until close completes. shutdown must wait for a
        # concurrent bounded drain before it can terminate the worker.
        with self._http_lock:
            if client not in self._http:
                return
            response = self._http[client]
            # Closing an HTTP object is not a completed TCP teardown. For our
            # short-lived HTTP/1.1 sessions actively send FIN and wait for peer FIN
            # before reaping the server. Never use abortive SO_LINGER or Windows
            # address sharing to make bind appear successful.
            operation = 'shutdown_write'
            try:
                network = response.extensions.get('network_stream') if response is not None else None
                peer = network.get_extra_info('socket') if network is not None else None
                if peer is not None and peer.fileno() >= 0:
                    deadline = time.monotonic() + .25
                    peer.settimeout(.25)
                    peer.shutdown(SHUT_WR)
                    operation = 'drain_peer'
                    remaining = 65536
                    while remaining and time.monotonic() < deadline:
                        peer.settimeout(max(.001, deadline - time.monotonic()))
                        data = peer.recv(min(8192, remaining))
                        if not data:
                            break
                        remaining -= len(data)
            except OSError as exc:
                # A peer may already be closed or not cooperate with cancellation.
                # Keep numeric evidence; final port bind still fails closed.
                self.transport_diagnostics.append({'operation': operation,
                    'errno': exc.errno, 'winerror': getattr(exc, 'winerror', None)})
                self.transport_diagnostics[:] = self.transport_diagnostics[-8:]
            finally:
                try:
                    client.close()
                finally:
                    self._http.pop(client, None)
                    self._completion_http.pop(client, None)

    def _interrupt_peer(self, peer):
        # close() alone need not wake another thread blocked in recv().
        # shutdown has no network wait, and the stream owner closes HTTPX.
        if peer is not None:
            try:
                peer.shutdown(SHUT_RDWR)
                if os.name == 'nt':
                    # Winsock shutdown() does not wake a recv() blocked in
                    # another thread; the stream then waits until a stuck
                    # llama-server closes. closesocket() fails that recv
                    # immediately (graceful close, no abortive SO_LINGER).
                    peer.close()
            except OSError as exc:
                self.transport_diagnostics.append({'operation': 'interrupt_generation',
                    'errno': exc.errno, 'winerror': getattr(exc, 'winerror', None)})
                self.transport_diagnostics[:] = self.transport_diagnostics[-8:]

    def _completion_trace(self, client, event, info):
        # The pinned httpcore trace extension exposes the socket before the
        # response headers exist, so cancellation also wakes slow prefill/header
        # reads. Each completion has its own fresh client/connection.
        if event != 'connection.connect_tcp.complete':
            return
        peer = info['return_value'].get_extra_info('socket')
        with self._http_lock:
            if client in self._completion_http:
                self._completion_http[client] = peer
            if self._cancelled.is_set():
                self._interrupt_peer(peer)

    def health(self) -> bool:
        process = self.process
        if process is None or process.poll() is not None:
            return False
        try:
            with self._client(0.5) as client:
                response = client.get(self.url + '/health', headers={'Connection': 'keep-alive'})
                self._response(client, response)
                data = response.json()
                return (response.status_code == 200 and isinstance(data, dict)
                        and data.get('status') == 'ok' and self.process is process)
        except (httpx.HTTPError, ValueError):
            return False
        except RuntimeError:
            # HTTPX rejects send() after shutdown has closed a registered
            # client. A stopped/replaced owner is an unhealthy runtime.
            if self.process is not process:
                return False
            raise

    def _check_port_available(self):
        # Retry only short teardown transients after reaping our own worker.
        # First startup, permission errors and unrelated faults never retry.
        # No new process is spawned until this same configured port binds.
        deadline = min(self._last_reaped_at + 2.0,
                       time.monotonic() + self.config.startup_timeout)
        attempts = 0
        errors = []
        self.last_bind_error = None
        while True:
            operation = 'socket'
            attempts += 1
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    if os.name != 'nt':
                        operation = 'setsockopt'
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    operation = 'bind'
                    probe.bind(('127.0.0.1', self.config.port))
                self.last_bind_diagnostics = {'port': self.config.port, 'attempts': attempts,
                                              'status': 'AVAILABLE', 'errors': list(errors)}
                return
            except OSError as exc:
                winerror = getattr(exc, 'winerror', None)
                in_use = (winerror == 10048 if winerror is not None
                          else exc.errno in (errno.EADDRINUSE, 10048))
                self.last_bind_error = exc
                errors.append({'operation': operation, 'errno': exc.errno, 'winerror': winerror})
                errors[:] = errors[-16:]
                self.last_bind_diagnostics = {'port': self.config.port, 'attempts': attempts,
                    'status': 'BLOCKED', 'errno': exc.errno, 'winerror': winerror, 'errors': list(errors)}
                if operation == 'bind' and in_use and time.monotonic() < deadline:
                    if self._startup_cancelled.wait(min(.02, max(0, deadline-time.monotonic()))):
                        raise RuntimeFailure('STARTUP_CANCELLED') from exc
                    continue
                code = ('PORT_IN_USE' if in_use else 'PORT_BIND_FAILED') if operation == 'bind' else 'PORT_PROBE_FAILED'
                failure = RuntimeFailure(code, os_error=exc, operation=operation)
                failure.diagnostics = dict(self.last_bind_diagnostics)
                raise failure from exc

    def start(self):
        if not self._starting.acquire(blocking=False):
            raise RuntimeFailure('START_IN_PROGRESS')
        try:
            # Serialize only ownership changes. Poll outside this lock so stop
            # never waits for the complete model-loading deadline.
            with self._lifecycle:
                if self.health():
                    return
                self.shutdown()
                self.validate()
                self._startup_cancelled.clear()
                self._check_port_available()
                try:
                    self.process = subprocess.Popen(
                        self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, env=self.child_environment(),
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    )
                except OSError as exc:
                    raise RuntimeFailure('RUNTIME_START_FAILED', os_error=exc, operation='spawn') from exc
                process = self.process
            deadline = time.monotonic() + self.config.startup_timeout
            while time.monotonic() < deadline:
                if self._startup_cancelled.is_set():
                    raise RuntimeFailure('STARTUP_CANCELLED')
                if process.poll() is not None:
                    failure=RuntimeFailure('RUNTIME_EXITED')
                    failure.exit_code=process.returncode
                    raise failure
                if self.health():
                    return
                self._startup_cancelled.wait(0.05)
            raise RuntimeFailure('STARTUP_TIMEOUT')
        except OSError as exc:
            self.shutdown()
            raise RuntimeFailure('RUNTIME_START_FAILED', os_error=exc, operation='start') from exc
        except BaseException:
            self.shutdown()
            raise
        finally:
            self._starting.release()

    def stream(self, prompt: str, max_tokens: int | None = None) -> Iterator[StreamEvent]:
        if not self._generation.acquire(blocking=False):
            raise RuntimeFailure('RUNTIME_BUSY')
        with self._http_lock:
            self._cancelled.clear()
            self._generation_active = True
        process = self.process
        try:
            if not self.health():
                raise RuntimeFailure('RUNTIME_UNAVAILABLE')
            if self._cancelled.is_set():
                return
            max_tokens = self.config.max_tokens if max_tokens is None else max_tokens
            if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
                raise ValueError('Invalid token limit')
            payload = {'prompt': prompt, 'n_predict': max_tokens, 'stream': True,
                       'cache_prompt': False, 'seed': 42, 'temperature': self.config.temperature,
                       'top_p':self.config.top_p, 'min_p':self.config.min_p, 'top_k':self.config.top_k,
                       'repeat_penalty':self.config.repeat_penalty}
            with self._client(httpx.Timeout(60, connect=2), completion=True) as client:
                if self._cancelled.is_set():
                    return
                with client.stream('POST', self.url + '/completion', json=payload,
                                   headers={'Connection': 'keep-alive'},
                                   extensions={'trace': lambda event, info:
                                       self._completion_trace(client, event, info)}) as response:
                    self._response(client, response)
                    if response.status_code != 200:
                        raise RuntimeFailure('GENERATION_REJECTED')
                    ended = False
                    for line in response.iter_lines():
                        if self._cancelled.is_set():
                            return
                        if not line or line.startswith(':'):
                            continue
                        if not line.startswith('data: '):
                            raise RuntimeFailure('INVALID_STREAM')
                        try:
                            data = json.loads(line[6:])
                            if not isinstance(data, dict) or not isinstance(data.get('content', ''), str):
                                raise ValueError()
                            if 'error' in data:
                                raise RuntimeFailure('GENERATION_FAILED')
                            event = StreamEvent(data.get('content', ''), data.get('stop') is True,
                                                data.get('tokens_predicted'), data.get('timings'))
                        except (ValueError, TypeError):
                            raise RuntimeFailure('INVALID_STREAM') from None
                        if event.stop:
                            ended = True
                            # A terminal event must not leave a live connection
                            # in a generator suspended by its caller.
                            self._close_client(client)
                        yield event
                        if event.stop:
                            break
                    if not ended and not self._cancelled.is_set():
                        raise RuntimeFailure('STREAM_INTERRUPTED')
        except httpx.HTTPError:
            if not self._cancelled.is_set():
                raise RuntimeFailure('RUNTIME_IO_ERROR') from None
        except RuntimeError as exc:
            if self._cancelled.is_set():
                return
            if not isinstance(exc, RuntimeFailure) and self.process is not process:
                raise RuntimeFailure('RUNTIME_IO_ERROR') from None
            raise
        finally:
            with self._http_lock:
                self._generation_active = False
            self._generation.release()

    @staticmethod
    def _consume_chat_metrics(data: dict, state: dict) -> None:
        """Accumulate validated OpenAI-compatible streamed usage and finish metadata."""
        usage=data.get('usage')
        if isinstance(usage,dict):
            for name in ('prompt_tokens','completion_tokens'):
                value=usage.get(name)
                if type(value) is int and 0 <= value <= 1000000:
                    state[name]=value
        choices=data.get('choices')
        if isinstance(choices,list) and choices and isinstance(choices[0],dict):
            reason=choices[0].get('finish_reason')
            if isinstance(reason,str) and reason:
                state['finish_reason']=reason
        # llama-server adds its own decode timings to the final chunk; keep only
        # validated numbers so monitoring/benchmark show real decode speed.
        timings=data.get('timings')
        if isinstance(timings,dict):
            for name in ('prompt_n','predicted_n','prompt_ms','predicted_ms','predicted_per_second'):
                value=timings.get(name)
                if type(value) in (int,float) and math.isfinite(value) and 0 <= value <= 1e9:
                    state.setdefault('timings',{})[name]=value

    @staticmethod
    def _chat_timings(state: dict) -> dict:
        timings={'prompt_n':state['prompt_tokens'],'predicted_n':state['completion_tokens']}
        timings.update({k:v for k,v in (state.get('timings') or {}).items() if v is not None})
        return timings

    def stream_chat(self, messages: list[dict], max_tokens: int | None = None,
                    sampling: dict | None = None) -> Iterator[StreamEvent]:
        """Chat-format generation; llama-server selects the GGUF chat template.

        Does not print the messages, and uses the existing cancellable HTTP
        connection ownership protocol to preserve Persistent Runtime.
        """
        if not isinstance(messages, list) or not messages or any(
            not isinstance(m, dict) or m.get('role') not in ('system','user','assistant')
            or not isinstance(m.get('content'),str) for m in messages
        ):
            raise ValueError('Invalid chat messages')
        if not self._generation.acquire(blocking=False):
            raise RuntimeFailure('RUNTIME_BUSY')
        with self._http_lock:
            self._cancelled.clear()
            self._generation_active=True
        process=self.process
        try:
            if not self.health():
                raise RuntimeFailure('RUNTIME_UNAVAILABLE')
            max_tokens=self.config.max_tokens if max_tokens is None else max_tokens
            if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
                raise ValueError('Invalid token limit')
            payload={'messages':messages,'max_tokens':max_tokens,'stream':True,
                     'stream_options':{'include_usage':True},
                     'temperature':self.config.temperature,'top_p':self.config.top_p,
                     'top_k':self.config.top_k,'min_p':self.config.min_p,'repeat_penalty':self.config.repeat_penalty,
                     # 2026-11-06 anti-loop: a light DRY penalty on long verbatim repeats of the context
                     # (strong DRY garbles Russian on Model D (8B); measured). Unknown to old servers: ignored.
                     'dry_multiplier':0.25,'dry_base':1.75,'dry_allowed_length':5,'dry_penalty_last_n':4096,'repeat_last_n':256}
            # Per-chat preset (stage 4a): only these sampling keys may be overridden.
            for key in ('temperature','top_p','top_k','min_p','repeat_penalty'):
                if sampling and key in sampling: payload[key]=sampling[key]
            with self._client(httpx.Timeout(60,connect=2), completion=True) as client:
                if self._cancelled.is_set(): return
                with client.stream('POST',self.url+'/v1/chat/completions',json=payload,
                                   headers={'Connection':'keep-alive'},
                                   extensions={'trace':lambda event,info:self._completion_trace(client,event,info)}) as response:
                    self._response(client,response)
                    if response.status_code!=200: raise RuntimeFailure('GENERATION_REJECTED')
                    ended=False
                    state={'finish_reason':None,'prompt_tokens':None,'completion_tokens':None}
                    terminal_seen=False
                    for line in response.iter_lines():
                        if self._cancelled.is_set(): return
                        if not line or line.startswith(':'): continue
                        if not line.startswith('data: '): raise RuntimeFailure('INVALID_STREAM')
                        if line[6:].strip()=='[DONE]':
                            ended=True
                            yield StreamEvent('',True,tokens_predicted=state['completion_tokens'],
                                              timings=self._chat_timings(state),
                                              finish_reason=state['finish_reason'])
                            break
                        try:
                            data=json.loads(line[6:])
                            if not isinstance(data,dict) or 'error' in data: raise ValueError()
                            self._consume_chat_metrics(data,state)
                            choices=data.get('choices')
                            if not isinstance(choices,list): raise ValueError()
                            if not choices:
                                # Final usage-only event typically follows finish_reason.
                                if not terminal_seen and not isinstance(data.get('usage'),dict):
                                    raise ValueError()
                                continue
                            choice=choices[0]
                            if not isinstance(choice,dict): raise ValueError()
                            fragment=choice.get('delta',{}).get('content')
                            if fragment is None: fragment=''
                            if not isinstance(fragment,str): raise ValueError()
                            terminal_seen=terminal_seen or choice.get('finish_reason') is not None
                        except (ValueError,TypeError,KeyError,AttributeError,IndexError):
                            raise RuntimeFailure('INVALID_STREAM') from None
                        if fragment: yield StreamEvent(fragment)
                    if terminal_seen and not ended and not self._cancelled.is_set():
                        # Some llama.cpp builds omit [DONE] after finish_reason.
                        ended=True
                        yield StreamEvent('',True,tokens_predicted=state['completion_tokens'],
                                          timings=self._chat_timings(state),
                                          finish_reason=state['finish_reason'])
                    if not ended and not self._cancelled.is_set():
                        raise RuntimeFailure('STREAM_INTERRUPTED')
        except httpx.HTTPError:
            if not self._cancelled.is_set():
                raise RuntimeFailure('RUNTIME_IO_ERROR') from None
        finally:
            with self._http_lock: self._generation_active=False
            self._generation.release()

    def count_tokens(self, text: str) -> int:
        """Exact token count from the loaded model's tokenizer (llama-server /tokenize).

        Needs no generation slot, so it is safe while another request streams.
        Text is sent only to the owned loopback child, never logged.
        """
        if not isinstance(text, str):
            raise ValueError('Invalid text')
        if self.process is None or self.process.poll() is not None:
            raise RuntimeFailure('RUNTIME_UNAVAILABLE')
        try:
            with httpx.Client(trust_env=False, timeout=httpx.Timeout(5, connect=1)) as client:
                response = client.post(self.url + '/tokenize', json={'content': text})
        except httpx.HTTPError:
            raise RuntimeFailure('RUNTIME_IO_ERROR') from None
        tokens = response.json().get('tokens') if response.status_code == 200 else None
        if not isinstance(tokens, list):
            raise RuntimeFailure('TOKENIZE_REJECTED')
        return len(tokens)

    def cancel_generation(self):
        """Disconnect only the active completion; preserve the owned worker.

        This interrupts our transport, not a confirmed backend slot cancel.
        llama.cpp builds may finish prefill or retain a stuck slot after a
        disconnect. Subsequent rejection/timeout is reported normally; the
        caller can explicitly restart its owned runtime. No hidden retry,
        invented slot-cancel endpoint, or foreign process termination occurs.
        """
        with self._http_lock:
            if not self._generation_active:
                return
            self._cancelled.set()
            for peer in self._completion_http.values():
                self._interrupt_peer(peer)

    def cancel(self):
        # M0 deliberately kills the owned worker to guarantee cancellation and
        # discard KV state. Reload is required. No promise of fast slot cancel.
        self._cancelled.set()
        self.shutdown()

    def shutdown(self):
        self._startup_cancelled.set()
        self.cancel_generation()
        with self._lifecycle:
            process, self.process = self.process, None
            with self._http_lock:
                clients = list(self._http)
            for client in clients:
                self._close_client(client)
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                else:
                    process.wait()
                self._last_reaped_at = time.monotonic()
