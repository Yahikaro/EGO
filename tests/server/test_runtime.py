import os
import socket
import sys
import threading
from pathlib import Path

import pytest

from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeConfig, RuntimeFailure
from runtime.adapters.ports import choose_loopback_port


def free_port():
    return choose_loopback_port()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    model = tmp_path / 'synthetic.gguf'
    model.write_bytes(b'GGUF' + b'\0' * 64)
    config = RuntimeConfig(Path(sys.executable), model, free_port(), startup_timeout=5)
    r = LlamaRuntime(config)
    fixture = Path(__file__).resolve().parents[2] / 'tests/fixtures/fake_llama.py'
    monkeypatch.setattr(r, '_binary_command', lambda: [getattr(sys,'_base_executable',sys.executable),str(fixture)])
    yield r
    r.shutdown()


def test_missing_model_blocks_before_spawn(tmp_path):
    r = LlamaRuntime(RuntimeConfig(Path(sys.executable), tmp_path / 'absent.gguf', free_port()))
    with pytest.raises(RuntimeFailure, match='MODEL_NOT_PROVIDED'):
        r.start()
    assert r.process is None


def test_invalid_model_and_binary(tmp_path):
    model = tmp_path / 'bad.gguf'
    model.write_bytes(b'bad!')
    r = LlamaRuntime(RuntimeConfig(Path(sys.executable), model, free_port()))
    with pytest.raises(RuntimeFailure, match='INVALID_GGUF'):
        r.start()
    model.write_bytes(b'GGUF')
    r = LlamaRuntime(RuntimeConfig(tmp_path / 'absent.exe', model, free_port()))
    with pytest.raises(RuntimeFailure, match='RUNTIME_NOT_PROVIDED'):
        r.start()


def test_loopback_command_and_sanitized_environment(runtime, monkeypatch):
    monkeypatch.setenv('LLAMA_ARG_HOST', '0.0.0.0')
    monkeypatch.setenv('LLAMA_LOG_VERBOSITY', '99')
    monkeypatch.setenv('HF_TOKEN', 'synthetic-never-inherit')
    command = runtime.command()
    assert command[command.index('--host') + 1] == '127.0.0.1'
    assert command[command.index('--parallel') + 1] == '1'
    assert not any(k.startswith(('LLAMA_', 'HF_')) for k in runtime.child_environment())


def test_lifecycle_stream_restart(runtime):
    assert not runtime.health()
    runtime.start()
    assert runtime.health()
    events = list(runtime.stream('synthetic'))
    assert ''.join(e.content for e in events) == 'xxx'
    assert events[-1].stop
    old = runtime.process
    runtime.shutdown()
    assert old.poll() is not None
    assert not runtime.health()
    runtime.start()
    assert runtime.health()


def test_cancel_active_stream_kills_worker_and_allows_restart(runtime):
    runtime.start()
    seen, errors = [], []
    first = threading.Event()

    def consume():
        try:
            for event in runtime.stream('slow'):
                seen.append(event)
                first.set()
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=consume)
    t.start()
    assert first.wait(3)
    worker = runtime.process
    runtime.cancel()
    t.join(3)
    assert not t.is_alive()
    assert not errors
    assert worker.poll() is not None
    assert len(seen) < 100
    runtime.start()
    assert runtime.health()


def test_malformed_stream_is_normalized(runtime):
    runtime.start()
    with pytest.raises(RuntimeFailure, match='INVALID_STREAM'):
        list(runtime.stream('malformed'))


def test_startup_failure_cleans_child(runtime, monkeypatch):
    monkeypatch.setattr(runtime, '_binary_command', lambda: [getattr(sys,'_base_executable',sys.executable),'-c', 'raise SystemExit(2)'])
    with pytest.raises(RuntimeFailure, match='RUNTIME_EXITED'):
        runtime.start()
    assert runtime.process is None


def test_startup_timeout_cleans_child(runtime, monkeypatch):
    monkeypatch.setattr(runtime, '_binary_command', lambda: [getattr(sys,'_base_executable',sys.executable),'-c', 'import time;time.sleep(20)'])
    object.__setattr__(runtime.config, 'startup_timeout', 0.15)
    with pytest.raises(RuntimeFailure, match='STARTUP_TIMEOUT'):
        runtime.start()
    assert runtime.process is None


def test_crash_restart(runtime):
    runtime.start()
    runtime.process.kill()
    runtime.process.wait()
    assert not runtime.health()
    runtime.start()
    assert runtime.health()


def test_port_already_used_is_not_mistaken_for_our_worker(runtime):
    with socket.socket() as s:
        s.bind(('127.0.0.1', runtime.config.port))
        s.listen()
        with pytest.raises(RuntimeFailure, match='PORT_IN_USE'):
            runtime.start()


def test_cancel_during_startup_is_bounded(runtime, monkeypatch):
    import time
    monkeypatch.setattr(runtime, '_binary_command', lambda: [getattr(sys,'_base_executable',sys.executable),'-c', 'import time;time.sleep(20)'])
    object.__setattr__(runtime.config, 'startup_timeout', 1.5)
    errors = []

    def start():
        try:
            runtime.start()
        except RuntimeFailure as exc:
            errors.append(exc.code)

    t = threading.Thread(target=start)
    t.start()
    deadline = time.monotonic() + 2
    while runtime.process is None and t.is_alive() and time.monotonic() < deadline:
        time.sleep(.005)
    child = runtime.process
    assert child is not None
    started = time.monotonic()
    runtime.cancel()
    elapsed = time.monotonic() - started
    t.join(2)
    assert not t.is_alive()
    assert child.poll() is not None
    assert elapsed < .75
    assert errors == ['STARTUP_CANCELLED']


def test_malformed_health_is_unhealthy_not_exception(runtime, monkeypatch):
    import httpx
    runtime.start()
    original = httpx.Client
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=transport, **kwargs))
    assert runtime.health() is False


@pytest.mark.parametrize('crash', [False, True], ids=['orderly', 'crash'])
def test_repeated_immediate_restart_reuses_configured_endpoint(runtime, crash):
    endpoint = runtime.url
    port = runtime.config.port
    for _ in range(5):
        runtime.start()
        worker = runtime.process
        assert runtime.health()
        assert ''.join(event.content for event in runtime.stream('synthetic')) == 'xxx'
        if crash:
            worker.kill()
            worker.wait(timeout=3)
        else:
            runtime.shutdown()
        assert worker.poll() is not None
        assert runtime.url == endpoint
        assert runtime.config.port == port
    runtime.start()
    assert runtime.health()


def test_fixture_leaves_health_connection_open_for_client_close(runtime):
    """Catch server-active HTTP/1.0 close and resulting server-side TIME_WAIT."""
    import http.client
    runtime.start()
    client = http.client.HTTPConnection('127.0.0.1', runtime.config.port, timeout=2)
    try:
        client.request('GET', '/health')
        response = client.getresponse()
        assert response.read() == b'{"status":"ok"}'
        assert response.will_close is False
        assert client.sock is not None
    finally:
        client.close()


@pytest.mark.parametrize('listening', [False, True], ids=['bound-only', 'listening'])
def test_real_occupied_port_retains_original_os_error(runtime, listening):
    import errno
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
        owner.bind(('127.0.0.1', runtime.config.port))
        if listening:
            owner.listen()
        with pytest.raises(RuntimeFailure) as caught:
            runtime.start()
    failure = caught.value
    assert failure.code == 'PORT_IN_USE'
    assert isinstance(failure.__cause__, OSError)
    assert failure.os_error is failure.__cause__
    assert failure.errno in (errno.EADDRINUSE, 10048)
    assert failure.operation == 'bind'
    assert runtime.process is None


@pytest.mark.parametrize('number, winerror, code', [
    (13, None, 'PORT_BIND_FAILED'),
    (99, None, 'PORT_BIND_FAILED'),
    (22, None, 'PORT_BIND_FAILED'),
    (13, 10013, 'PORT_BIND_FAILED'),
    (10048, 10048, 'PORT_IN_USE'),
])
def test_bind_errors_keep_diagnostics_and_are_not_all_port_in_use(runtime, monkeypatch, number, winerror, code):
    from types import SimpleNamespace
    from runtime.adapters import llama_cpp as adapter
    original = OSError(number, 'synthetic socket-boundary failure')
    if winerror is not None:
        original.winerror = winerror

    class FailingSocket:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            assert address == ('127.0.0.1', runtime.config.port)
            raise original

    # Inject only this adapter's socket boundary; no real Windows run implied.
    monkeypatch.setattr(adapter, 'socket', SimpleNamespace(socket=FailingSocket,
        AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM,
        SOL_SOCKET=socket.SOL_SOCKET, SO_REUSEADDR=socket.SO_REUSEADDR))
    with pytest.raises(RuntimeFailure) as caught:
        runtime.start()
    failure = caught.value
    assert failure.code == code
    assert failure.errno == number
    assert failure.winerror == winerror
    assert failure.os_error is original
    assert failure.__cause__ is original
    assert failure.operation == 'bind'
    assert runtime.process is None


def test_spawn_os_error_keeps_original_cause(runtime, monkeypatch):
    from runtime.adapters import llama_cpp as adapter
    import errno
    original = OSError(errno.EACCES, 'synthetic executable permission denial')

    def cannot_spawn(*args, **kwargs):
        raise original

    monkeypatch.setattr(adapter.subprocess, 'Popen', cannot_spawn)
    with pytest.raises(RuntimeFailure) as caught:
        runtime.start()
    assert caught.value.code == 'RUNTIME_START_FAILED'
    assert caught.value.errno == errno.EACCES
    assert caught.value.__cause__ is original
    assert caught.value.operation == 'spawn'


def test_terminal_stream_releases_tcp_before_caller_resumes(runtime, monkeypatch):
    """A consumer may stop advancing the generator at its terminal event."""
    sockets = []
    register_response = runtime._response

    def capture_completion_socket(client, response):
        register_response(client, response)
        if response.request.url.path == '/completion':
            peer = response.extensions['network_stream'].get_extra_info('socket')
            assert peer.fileno() >= 0
            sockets.append(peer)

    monkeypatch.setattr(runtime, '_response', capture_completion_socket)
    runtime.start()
    endpoint, port = runtime.url, runtime.config.port
    iterator = runtime.stream('synthetic')
    try:
        while not next(iterator).stop:
            pass
        # Check the actual transport before shutdown can close it for us. An
        # independent raw bind is not a portable TCP-cleanup oracle: Windows
        # exclusive-listener teardown can outlive the application's handles.
        assert len(sockets) == 1
        assert sockets[0].fileno() == -1
        assert not runtime._http
        worker = runtime.process
        runtime.shutdown()
        assert worker.poll() is not None
        # Exercise the supported restart path on that exact endpoint while the
        # original generator is still suspended at its terminal event.
        runtime.start()
        assert runtime.process is not worker
        assert runtime.health()
        assert runtime.url == endpoint
        assert runtime.config.port == port
    finally:
        iterator.close()


def test_recent_owned_port_waits_for_transient_drain_without_changing_endpoint(runtime, monkeypatch):
    import errno
    import time
    from runtime.adapters import llama_cpp as adapter
    runtime.start()
    runtime.shutdown()
    original_socket = socket.socket
    failures = [OSError(errno.EADDRINUSE, 'synthetic accepted socket draining')]
    attempts = []

    class Probe:
        def __init__(self, *args):
            self.inner = original_socket(*args)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.inner.close()
        def setsockopt(self, *args):
            self.inner.setsockopt(*args)
        def bind(self, address):
            attempts.append(address)
            if failures:
                raise failures.pop()
            self.inner.bind(address)

    # Only adapter's preflight socket is controlled; the replacement worker
    # and all HTTP requests use real subprocesses and sockets.
    from types import SimpleNamespace
    monkeypatch.setattr(adapter, 'socket', SimpleNamespace(socket=Probe,
        AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM,
        SOL_SOCKET=socket.SOL_SOCKET, SO_REUSEADDR=socket.SO_REUSEADDR))
    runtime.start()
    assert runtime.health()
    assert len(attempts) == 2
    assert all(address == ('127.0.0.1', runtime.config.port) for address in attempts)


@pytest.mark.parametrize('listening', [False, True], ids=['bound-only', 'listening'])
def test_restart_does_not_hide_new_real_listener_on_the_owned_port(runtime, tmp_path, monkeypatch, listening):
    # Start a real owned listener without accepting HTTP connections. Otherwise
    # obtaining the foreign listener below first assumes immediate raw rebinding
    # after Windows exclusive-listener TCP teardown, which is not our contract.
    ready = tmp_path / 'listener.ready'
    command = runtime._binary_command()
    health = runtime.health
    monkeypatch.setattr(runtime, '_binary_command',
                        lambda: command + ['--ready-file', str(ready)])
    monkeypatch.setattr(runtime, 'health', lambda: (
        runtime.process is not None and runtime.process.poll() is None and ready.is_file()))
    runtime.start()
    worker = runtime.process
    runtime.shutdown()
    monkeypatch.setattr(runtime, 'health', health)
    assert worker.poll() is not None
    assert runtime._last_reaped_at > 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
        if os.name == 'nt':
            owner.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        owner.bind(('127.0.0.1', runtime.config.port))
        if listening:
            owner.listen()
        with pytest.raises(RuntimeFailure, match='PORT_IN_USE') as caught:
            runtime.start()
        assert isinstance(caught.value.__cause__, OSError)
        assert caught.value.os_error is caught.value.__cause__
        assert caught.value.operation == 'bind'
        assert caught.value.diagnostics['attempts'] > 1
        assert runtime.process is None


def test_completed_health_waits_for_peer_fin_before_return(tmp_path, monkeypatch):
    """HTTP close must complete before an external crash/restart is attempted."""
    import httpx
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from runtime.adapters import llama_cpp as adapter
    peer_closed = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass
        def do_GET(self):
            data = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
        def finish(self):
            # Simulate scheduling delay after receiving the client's FIN.
            time.sleep(.03)
            super().finish()
            peer_closed.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    r = LlamaRuntime(RuntimeConfig(Path(sys.executable), tmp_path/'unused', server.server_port))
    class Alive:
        def poll(self):
            return None
    r.process = Alive()
    try:
        assert r.health()
        assert peer_closed.is_set()
    finally:
        r.process = None
        server.shutdown()
        thread.join(2)
        server.server_close()


def test_port_retry_budget_preserves_last_error_and_never_spawns(runtime, monkeypatch):
    import errno
    import time
    from types import SimpleNamespace
    from runtime.adapters import llama_cpp as adapter
    original = OSError(errno.EADDRINUSE, 'synthetic persistent listener')
    original.winerror = 10048
    attempts = []
    runtime._last_reaped_at = time.monotonic()
    object.__setattr__(runtime.config, 'startup_timeout', .06)

    class Blocked:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setsockopt(self, *args): pass
        def bind(self, address):
            attempts.append(address)
            raise original

    monkeypatch.setattr(adapter, 'socket', SimpleNamespace(socket=lambda *args: Blocked(),
        AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM,
        SOL_SOCKET=socket.SOL_SOCKET, SO_REUSEADDR=socket.SO_REUSEADDR))
    started = time.monotonic()
    with pytest.raises(RuntimeFailure, match='PORT_IN_USE') as caught:
        runtime.start()
    assert time.monotonic()-started < .5
    assert len(attempts) > 1
    assert caught.value.__cause__ is original
    assert caught.value.winerror == 10048
    assert caught.value.diagnostics['attempts'] == len(attempts)
    assert runtime.process is None


def test_cancel_during_owned_port_drain_is_bounded(runtime, monkeypatch):
    import errno
    import time
    from types import SimpleNamespace
    from runtime.adapters import llama_cpp as adapter
    runtime._last_reaped_at = time.monotonic()
    entered = threading.Event()
    errors = []

    class Blocked:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setsockopt(self, *args): pass
        def bind(self, address):
            entered.set()
            raise OSError(errno.EADDRINUSE, 'synthetic drain')

    monkeypatch.setattr(adapter, 'socket', SimpleNamespace(socket=lambda *args: Blocked(),
        AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM,
        SOL_SOCKET=socket.SOL_SOCKET, SO_REUSEADDR=socket.SO_REUSEADDR))
    def start():
        try: runtime.start()
        except RuntimeFailure as exc: errors.append(exc.code)
    t = threading.Thread(target=start)
    t.start()
    assert entered.wait(1)
    before = time.monotonic()
    runtime.cancel()
    t.join(1)
    assert not t.is_alive()
    assert time.monotonic()-before < .5
    assert errors == ['STARTUP_CANCELLED']


def test_cancel_closes_registered_http_before_terminating_child(runtime, monkeypatch):
    runtime.start()
    stream = runtime.stream('slow')
    next(stream)
    child = runtime.process
    terminate = child.terminate
    def checked_terminate():
        assert not runtime._http
        terminate()
    monkeypatch.setattr(child, 'terminate', checked_terminate)
    try:
        runtime.cancel()
        assert child.poll() is not None
    finally:
        stream.close()


@pytest.mark.parametrize('request_kind', ['health', 'stream'])
def test_cancel_between_http_registration_and_send_is_normalized(runtime, monkeypatch, request_kind):
    import httpx
    runtime.start()
    entered, resume = threading.Event(), threading.Event()
    original_send = httpx.Client.send
    errors, results = [], []
    target = '/health' if request_kind == 'health' else '/completion'

    def paused_send(client, request, *args, **kwargs):
        if request.url.path == target:
            entered.set()
            assert resume.wait(3)
        return original_send(client, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, 'send', paused_send)
    def consume():
        try:
            results.append(runtime.health() if request_kind == 'health' else list(runtime.stream('slow')))
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=consume)
    thread.start()
    try:
        assert entered.wait(3)
        runtime.cancel()
    finally:
        resume.set()
        thread.join(3)
    assert not thread.is_alive()
    assert not errors
    assert results == ([False] if request_kind == 'health' else [[]])


def test_shutdown_waits_for_http_close_already_in_progress(tmp_path):
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    received_fin, peer_closed = threading.Event(), threading.Event()
    terminated_after_close, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args): pass
        def do_GET(self):
            data = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
        def finish(self):
            received_fin.set()
            time.sleep(.08)
            super().finish()
            peer_closed.set()

    class OwnedWorker:
        returncode = None
        def poll(self): return self.returncode
        def terminate(self):
            terminated_after_close.append(peer_closed.is_set())
            self.returncode = 0
        def wait(self, timeout=None): return self.returncode

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    r = LlamaRuntime(RuntimeConfig(Path(sys.executable), tmp_path/'unused', server.server_port))
    r.process = OwnedWorker()
    def health():
        try: r.health()
        except Exception as exc: errors.append(exc)
    request = threading.Thread(target=health)
    request.start()
    try:
        assert received_fin.wait(3)
        r.shutdown()
        request.join(2)
        assert not request.is_alive()
        assert not errors
        assert terminated_after_close == [True]
        assert not r._http
    finally:
        r.shutdown()
        request.join(2)
        server.shutdown()
        serving.join(2)
        server.server_close()
