"""Ephemeral-selection collisions must not overlap default client port space;
network faults must propagate instead of being silently retried as occupancy.
"""
import errno
import socket
from types import SimpleNamespace

import pytest


def test_selected_service_port_is_loopback_bindable_outside_default_client_range():
    from runtime.adapters import ports
    port = ports.choose_loopback_port()
    assert 10000 <= port < 30000
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', port))


def test_selector_retries_occupied_candidate_and_not_network_failure(monkeypatch):
    from runtime.adapters import ports
    choices = iter([0, 1])
    monkeypatch.setattr(ports.secrets, 'randbelow', lambda size: next(choices))
    calls = []

    class Candidate:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            calls.append(address)
            if address[1] == 10000:
                raise OSError(errno.EADDRINUSE, 'synthetic collision')

    monkeypatch.setattr(ports, 'socket', SimpleNamespace(socket=lambda *args: Candidate(),
        AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM,
        SOL_SOCKET=socket.SOL_SOCKET, SO_EXCLUSIVEADDRUSE=-5))
    assert ports.choose_loopback_port() == 10001
    assert calls == [('127.0.0.1', 10000), ('127.0.0.1', 10001)]

    original = OSError(errno.ENETDOWN, 'synthetic network unavailable')

    class NetworkDown(Candidate):
        def bind(self, address):
            raise original

    monkeypatch.setattr(ports.secrets, 'randbelow', lambda size: 0)
    ports.socket.socket = lambda *args: NetworkDown()
    with pytest.raises(OSError) as caught:
        ports.choose_loopback_port()
    assert caught.value is original


def test_selector_exhaustion_retains_original_permission_error(monkeypatch):
    from runtime.adapters import ports
    original = OSError(errno.EACCES, 'synthetic reserved range')
    original.winerror = 10013
    attempts = []

    class Reserved:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            attempts.append(address)
            raise original

    monkeypatch.setattr(ports, 'socket', SimpleNamespace(socket=lambda *args: Reserved(),
        AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM,
        SOL_SOCKET=socket.SOL_SOCKET, SO_EXCLUSIVEADDRUSE=-5))
    with pytest.raises(OSError) as caught:
        ports.choose_loopback_port()
    assert caught.value is original
    assert len(attempts) == 32
