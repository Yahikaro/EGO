"""Persistent adapter contract against a real disposable HTTP child."""
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeConfig, RuntimeFailure
from runtime.adapters.ports import choose_loopback_port


@pytest.fixture
def persistent_runtime(tmp_path, monkeypatch):
    model = tmp_path / 'synthetic.gguf'
    model.write_bytes(b'GGUF' + b'\0' * 64)
    runtime = LlamaRuntime(RuntimeConfig(Path(sys.executable), model,
                           choose_loopback_port(), startup_timeout=5))
    fixture = Path(__file__).resolve().parents[2] / 'tests/fixtures/fake_llama_persistent.py'
    monkeypatch.setattr(runtime, '_binary_command', lambda: [
        getattr(sys,'_base_executable',sys.executable), str(fixture), '--request-dir', str(tmp_path)])
    runtime.start()
    yield runtime, tmp_path
    runtime.shutdown()


def wait_file(path):
    deadline = time.monotonic() + 3
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.005)
    assert path.exists(), f'child did not reach {path.name}'


def consume(runtime, prompt):
    errors, events = [], []

    def run():
        try:
            events.extend(runtime.stream(prompt))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, errors, events


@pytest.mark.parametrize('prompt', ['block-body', 'block-headers'])
def test_soft_cancel_wakes_blocked_http_and_keeps_same_child(persistent_runtime, prompt):
    runtime, directory = persistent_runtime
    child = runtime.process
    thread, errors, _ = consume(runtime, prompt)
    wait_file(directory / prompt)
    before = time.monotonic()
    runtime.cancel_generation()
    assert time.monotonic() - before < .5
    thread.join(1)
    assert not thread.is_alive()
    assert not errors
    wait_file(directory / (prompt + '.closed'))
    assert runtime.process is child and child.poll() is None
    assert runtime.health()
    assert ''.join(event.content for event in runtime.stream('next')) == 'next'
    assert runtime.process is child
    assert not runtime._http


def test_idle_soft_cancel_does_not_poison_next_generation(persistent_runtime):
    runtime, _ = persistent_runtime
    child = runtime.process
    runtime.cancel_generation()
    runtime.cancel_generation()
    assert ''.join(event.content for event in runtime.stream('fresh')) == 'fresh'
    assert runtime.process is child and child.poll() is None


def test_soft_cancel_between_client_registration_and_send(persistent_runtime, monkeypatch):
    runtime, _ = persistent_runtime
    entered, resume = threading.Event(), threading.Event()
    send = httpx.Client.send

    def paused_send(client, request, *args, **kwargs):
        if request.url.path == '/completion':
            entered.set()
            assert resume.wait(3)
        return send(client, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, 'send', paused_send)
    thread, errors, events = consume(runtime, 'block-body')
    try:
        assert entered.wait(3)
        runtime.cancel_generation()
    finally:
        resume.set()
        thread.join(1)
    assert not thread.is_alive()
    assert not errors and not events
    assert ''.join(event.content for event in runtime.stream('fresh')) == 'fresh'


def test_cancel_during_terminal_yield_does_not_affect_following_request(persistent_runtime):
    runtime, _ = persistent_runtime
    stream = runtime.stream('done')
    try:
        assert next(stream).stop
        runtime.cancel_generation()
        assert list(stream) == []
        assert ''.join(event.content for event in runtime.stream('fresh')) == 'fresh'
    finally:
        stream.close()


def test_backend_stuck_slot_is_reported_without_killing_or_retrying(persistent_runtime):
    runtime, directory = persistent_runtime
    child = runtime.process
    thread, errors, _ = consume(runtime, 'stuck-slot')
    wait_file(directory / 'stuck-slot')
    runtime.cancel_generation()
    thread.join(1)
    assert not thread.is_alive() and not errors
    with pytest.raises(RuntimeFailure, match='GENERATION_REJECTED'):
        list(runtime.stream('next'))
    assert runtime.process is child and child.poll() is None
    wait_file(directory / 'stuck-slot.closed')
    assert ''.join(event.content for event in runtime.stream('recovered')) == 'recovered'
