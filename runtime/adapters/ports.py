"""Best-effort internal server ports, separate from default Windows client ports.
Not a reservation: the runtime must still check/bind the configured endpoint.
"""
import errno
import os
import secrets
import socket


def choose_loopback_port() -> int:
    # bind(port=0) normally chooses from49152..65535 on Windows, the same pool
    # used by HTTP clients. Avoid that default pool and recently used client
    # tuples. Custom OS dynamic/excluded ranges may differ; never alter them.
    last_error = None
    for _ in range(32):
        port = 10000 + secrets.randbelow(20000)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == 'nt':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError as exc:
                winerror = getattr(exc, 'winerror', None)
                # A candidate can be occupied or OS-reserved. A configured
                # port is never silently replaced by this chooser on restart.
                retry = (winerror in (10048, 10013) if winerror is not None
                         else exc.errno in (errno.EADDRINUSE, errno.EACCES, 10048, 10013))
                if not retry:
                    raise
                last_error = exc
                continue
        return port
    assert last_error is not None
    raise last_error
