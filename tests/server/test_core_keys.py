"""Vault tests use synthetic AEAD injection, never claim native Windows DPAPI."""
import base64
import importlib.util
import os
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def test_vault_module_exists():
    assert importlib.util.find_spec('server.ego_core.keys') is not None


def vault(directory, key=None, **kwargs):
    from server.ego_core.keys import KeyVault
    aead = AESGCM(key or b's' * 32)
    def protect(data):
        nonce = os.urandom(12)
        return nonce + aead.encrypt(nonce, data, b'test-only-user-scope')
    def unprotect(data):
        return aead.decrypt(data[:12], data[12:], b'test-only-user-scope')
    return KeyVault(directory, protect=protect, unprotect=unprotect, **kwargs)


def failure(code, fn):
    from server.ego_core.keys import KeyFailure
    with pytest.raises(KeyFailure) as caught:
        fn()
    assert caught.value.code == code
    assert str(caught.value) == code


def test_initialize_unlock_independent_secrets_encrypted_files(tmp_path):
    directory = tmp_path / 'vault'
    identity = vault(directory).initialize()
    assert vault(directory).unlock() == identity
    secrets = identity.secrets
    assert len(secrets.main_key) == len(secrets.profile_key) == 32
    assert secrets.main_key != secrets.profile_key
    assert len(base64.urlsafe_b64decode(secrets.admin_token + '==')) >= 32
    assert len(base64.urlsafe_b64decode(secrets.tls_password + '==')) >= 32
    assert secrets.admin_token != secrets.tls_password
    assert identity.pin == x509.load_pem_x509_certificate(identity.certfile.read_bytes()).fingerprint(hashes.SHA256()).hex()
    assert b'BEGIN ENCRYPTED PRIVATE KEY' in identity.keyfile.read_bytes()
    disk = b''.join(p.read_bytes() for p in directory.iterdir())
    for secret in (secrets.main_key, secrets.profile_key, secrets.admin_token.encode(), secrets.tls_password.encode()):
        assert secret not in disk
    assert b'BEGIN PRIVATE KEY' not in disk
    assert b'"main_key"' not in disk
    if os.name != 'nt':
        assert directory.stat().st_mode & 0o777 == 0o700
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir())


def test_certificate_is_loopback_server_identity(tmp_path):
    identity = vault(tmp_path / 'vault').initialize()
    cert = x509.load_pem_x509_certificate(identity.certfile.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ['localhost']
    assert [str(ip) for ip in san.get_values_for_type(x509.IPAddress)] == ['127.0.0.1']
    assert cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value == x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH])
    assert timedelta(days=364) < cert.not_valid_after_utc - cert.not_valid_before_utc <= timedelta(days=366)
    key = serialization.load_pem_private_key(identity.keyfile.read_bytes(), identity.secrets.tls_password.encode())
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    with pytest.raises(TypeError):
        serialization.load_pem_private_key(identity.keyfile.read_bytes(), None)


def test_real_tls_accepts_exact_trust_and_rejects_wrong_trust(tmp_path):
    identity = vault(tmp_path / 'one').initialize()
    wrong = vault(tmp_path / 'two').initialize()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'private core')
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(identity.certfile, identity.keyfile, identity.secrets.tls_password)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'https://localhost:{server.server_port}/'
        with httpx.Client(verify=ssl.create_default_context(cafile=str(identity.certfile)), trust_env=False) as client:
            assert client.get(url).text == 'private core'
        with httpx.Client(verify=ssl.create_default_context(cafile=str(wrong.certfile)), trust_env=False) as client:
            with pytest.raises(httpx.ConnectError):
                client.get(url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_native_dpapi_has_no_non_windows_fallback(tmp_path):
    from server.ego_core.keys import KeyVault
    if os.name == 'nt':
        pytest.skip('Native DPAPI/ACL acceptance is executed separately on Windows')
    failure('WINDOWS_DPAPI_REQUIRED', lambda: KeyVault(tmp_path / 'vault').initialize())
    assert not list(tmp_path.rglob('*'))


def test_wrong_protector_and_tampering_fail_closed(tmp_path):
    directory = tmp_path / 'vault'
    vault(directory).initialize()
    failure('VAULT_UNPROTECT_FAILED', lambda: vault(directory, b'w' * 32).unlock())
    protected = directory / 'keys.protected'
    data = protected.read_bytes()
    protected.write_bytes(data[:-1] + bytes([data[-1] ^ 0x01]))   # always a different byte (b'x' could equal it)
    failure('VAULT_UNPROTECT_FAILED', lambda: vault(directory).unlock())


@pytest.mark.parametrize('missing', ['keys.protected', 'identity.crt.pem', 'identity.key.pem'])
def test_partial_vault_never_regenerates(tmp_path, missing):
    directory = tmp_path / 'vault'
    vault(directory).initialize()
    (directory / missing).unlink()
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    failure('VAULT_INCOMPLETE', lambda: vault(directory).unlock())
    failure('VAULT_EXISTS', lambda: vault(directory).initialize())
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


@pytest.mark.parametrize('database', ['main.db', 'profile.db', 'nested/main.db', 'config.json'])
def test_existing_state_prevents_key_creation(tmp_path, database):
    directory = tmp_path / 'vault'
    target = directory / database
    target.parent.mkdir(parents=True)
    target.write_bytes(b'existing data')
    if database=='config.json':
        # M1.2 intentionally permits pre-bootstrap nonsecret configuration.
        # Keep this case: initialization must preserve it byte-for-byte.
        vault(directory).initialize()
        assert (directory/'keys.protected').is_file()
        assert target.read_bytes()==b'existing data'
        return
    failure('EXISTING_STATE', lambda: vault(directory).initialize())
    assert target.read_bytes() == b'existing data'
    assert not (directory / 'keys.protected').exists()


def test_missing_vault_is_not_initialization(tmp_path):
    failure('VAULT_MISSING', lambda: vault(tmp_path / 'missing').unlock())
    assert not (tmp_path / 'missing').exists()


def test_symlinks_and_broad_permissions_fail_closed(tmp_path):
    if os.name == 'nt':
        pytest.skip('Windows reparse checks require native acceptance')
    directory = tmp_path / 'vault'
    identity = vault(directory).initialize()
    identity.keyfile.chmod(0o644)
    failure('INSECURE_PERMISSIONS', lambda: vault(directory).unlock())
    identity.keyfile.chmod(0o600)
    outside = tmp_path / 'outside'
    outside.write_bytes(identity.keyfile.read_bytes())
    identity.keyfile.unlink()
    identity.keyfile.symlink_to(outside)
    failure('UNSAFE_PATH', lambda: vault(directory).unlock())
    linked = tmp_path / 'linked'
    linked.symlink_to(directory, target_is_directory=True)
    failure('UNSAFE_PATH', lambda: vault(linked).unlock())


def test_tampered_certificate_and_encrypted_key_fail_closed(tmp_path):
    identity = vault(tmp_path / 'vault').initialize()
    other = vault(tmp_path / 'other').initialize()
    identity.certfile.write_bytes(other.certfile.read_bytes())
    failure('TLS_IDENTITY_INVALID', lambda: vault(tmp_path / 'vault').unlock())
    identity.certfile.write_bytes(b'malformed certificate')
    failure('TLS_IDENTITY_INVALID', lambda: vault(tmp_path / 'vault').unlock())


def test_bounded_vault_read_and_fixed_errors(tmp_path):
    directory = tmp_path / 'vault'
    vault(directory).initialize()
    (directory / 'keys.protected').write_bytes(b'x' * 131073)
    failure('VAULT_TOO_LARGE', lambda: vault(directory).unlock())


def test_failed_initialization_cleans_only_owned_files(tmp_path):
    directory = tmp_path / 'vault'
    directory.mkdir()
    marker = directory / 'unrelated.txt'
    marker.write_bytes(b'preserve')
    def reject(_):
        raise OSError('sensitive path must not reach exception')
    failure('PRIVATE_PATH_FAILED', lambda: vault(directory, privatize=reject).initialize())
    assert marker.read_bytes() == b'preserve'
    assert {p.name for p in directory.iterdir()} == {'unrelated.txt'}


def _rewrite_protected(directory, change):
    import json
    protected = directory / 'keys.protected'
    data = protected.read_bytes()
    aead = AESGCM(b's' * 32)
    payload = json.loads(aead.decrypt(data[:12], data[12:], b'test-only-user-scope'))
    change(payload)
    nonce = os.urandom(12)
    protected.write_bytes(nonce + aead.encrypt(nonce, json.dumps(payload).encode(), b'test-only-user-scope'))


@pytest.mark.parametrize('days', [-10, 10])
def test_expired_or_not_yet_valid_certificate_fails_even_with_matching_pin(tmp_path, days):
    directory = tmp_path / 'vault'
    identity = vault(directory).initialize()
    key = serialization.load_pem_private_key(identity.keyfile.read_bytes(), identity.secrets.tls_password.encode())
    original = x509.load_pem_x509_certificate(identity.certfile.read_bytes())
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(original.subject).issuer_name(original.issuer)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now + timedelta(days=days)).not_valid_after(now + timedelta(days=days + 1)))
    for extension in original.extensions:
        builder = builder.add_extension(extension.value, extension.critical)
    replacement = builder.sign(key, hashes.SHA256())
    identity.certfile.write_bytes(replacement.public_bytes(serialization.Encoding.PEM))
    _rewrite_protected(directory, lambda payload: payload.update(pin=replacement.fingerprint(hashes.SHA256()).hex()))
    failure('TLS_IDENTITY_INVALID', lambda: vault(directory).unlock())


def test_certificate_key_mismatch_rejected_even_with_matching_pin(tmp_path):
    directory = tmp_path / 'vault'
    identity = vault(directory).initialize()
    other = vault(tmp_path / 'other').initialize()
    identity.certfile.write_bytes(other.certfile.read_bytes())
    _rewrite_protected(directory, lambda payload: payload.update(pin=other.pin))
    failure('TLS_IDENTITY_INVALID', lambda: vault(directory).unlock())


def test_encrypted_private_key_tamper_is_rejected(tmp_path):
    directory = tmp_path / 'vault'
    identity = vault(directory).initialize()
    identity.keyfile.write_bytes(b'-----BEGIN ENCRYPTED PRIVATE KEY-----\ninvalid\n')
    failure('TLS_IDENTITY_INVALID', lambda: vault(directory).unlock())


def test_protect_failure_publishes_no_identity_or_plaintext(tmp_path):
    from server.ego_core.keys import KeyVault
    directory = tmp_path / 'vault'
    def reject(_):
        raise RuntimeError('do not disclose me')
    failure('VAULT_PROTECT_FAILED', lambda: KeyVault(directory, protect=reject, unprotect=reject).initialize())
    assert not list(directory.iterdir())


def test_second_file_security_failure_removes_own_publication_only(tmp_path):
    directory = tmp_path / 'vault'
    directory.mkdir()
    marker = directory / 'unrelated.txt'
    marker.write_text('keep')
    calls = []
    def secure(path):
        calls.append(path)
        if len(calls) == 3:
            raise OSError('stop before second publish')
        path.chmod(0o700 if path.is_dir() else 0o600)
    failure('PRIVATE_PATH_FAILED', lambda: vault(directory, privatize=secure).initialize())
    assert {p.name for p in directory.iterdir()} == {'unrelated.txt'}
    assert marker.read_text() == 'keep'


def test_file_publication_closes_windows_incompatible_open_handle_before_unlink(tmp_path, monkeypatch):
    # Emulate Windows file-sharing semantics: deleting an open file is denied.
    from server.ego_core import keys
    opened = set()
    original_open, original_close, original_unlink = keys.os.open, keys.os.close, Path.unlink
    def tracked_open(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        opened.add((str(path), fd))
        return fd
    def tracked_close(fd):
        opened.difference_update((path, number) for path, number in tuple(opened) if number == fd)
        return original_close(fd)
    def windows_unlink(path, *args, **kwargs):
        if any(name == str(path) for name, _ in opened):
            raise PermissionError('Windows file is in use')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(keys.os, 'open', tracked_open)
    monkeypatch.setattr(keys.os, 'close', tracked_close)
    monkeypatch.setattr(Path, 'unlink', windows_unlink)
    identity = vault(tmp_path / 'vault').initialize()
    assert vault(tmp_path / 'vault').unlock() == identity
    assert not opened
