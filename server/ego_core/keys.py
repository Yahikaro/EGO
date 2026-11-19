"""User-DPAPI key vault and private loopback TLS identity.

``Identity.pin`` is lower-case SHA-256 of the complete DER certificate. Only the
DPAPI-wrapped payload contains database keys, bearer token, and PEM password.
Dependency injection is for embedding/tests, never a production CLI fallback.
"""
from __future__ import annotations

import base64
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import secrets as random_secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


class KeyFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = self.strcode = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class Secrets:
    main_key: bytes
    profile_key: bytes
    admin_token: str
    tls_password: str


@dataclass(frozen=True)
class Identity:
    secrets: Secrets
    certfile: Path
    keyfile: Path
    pin: str


def _unsafe(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _check_path(path: Path) -> None:
    for part in reversed((path, *path.parents)):
        try:
            if _unsafe(part):
                raise KeyFailure('UNSAFE_PATH')
        except FileNotFoundError:
            continue


def _windows_private(path: Path) -> None:
    """Set a protected DACL granting only the actual process token user access.

    Uses Win32 token/SID/ACL APIs, never USERNAME, PATH resolution, or a shell.
    Native Windows execution is a separate acceptance requirement.
    """
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    token, sid_text, descriptor = wintypes.HANDLE(), wintypes.LPWSTR(), ctypes.c_void_p()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise KeyFailure('PRIVATE_PATH_FAILED')
        length = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        if not 0 < length.value < 65536:
            raise KeyFailure('PRIVATE_PATH_FAILED')
        buffer = ctypes.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, buffer, length, ctypes.byref(length)):
            raise KeyFailure('PRIVATE_PATH_FAILED')
        # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is PSID.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise KeyFailure('PRIVATE_PATH_FAILED')
        inherit = 'OICI' if path.is_dir() else ''
        sddl = f'D:P(A;{inherit};FA;;;{sid_text.value})'
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise KeyFailure('PRIVATE_PATH_FAILED')
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise KeyFailure('PRIVATE_PATH_FAILED')
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)


def _privatize(path: Path) -> None:
    _check_path(path)
    if os.name == 'nt':
        _windows_private(path)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


class KeyVault:
    VAULT = 'keys.protected'
    CERT = 'identity.crt.pem'
    KEY = 'identity.key.pem'
    MAX_READ = 131072

    def __init__(self, directory: Path, *, protect=None, unprotect=None, privatize=None):
        self.directory = Path(os.path.abspath(directory))
        if (protect is None) != (unprotect is None):
            raise KeyFailure('INVALID_PROTECTOR')
        self._native = protect is None
        if self._native:
            from server.ego_core.vault.crypto_restore.dpapi import protect, unprotect
        self._protect, self._unprotect = protect, unprotect
        self._privatize = privatize or _privatize

    def _platform(self):
        if self._native and os.name != 'nt':
            raise KeyFailure('WINDOWS_DPAPI_REQUIRED')

    def _private(self, path):
        try:
            _check_path(path)
            self._privatize(path)
        except KeyFailure:
            raise
        except Exception:
            raise KeyFailure('PRIVATE_PATH_FAILED') from None

    def _permissions(self, path):
        _check_path(path)
        info = path.lstat()
        if os.name == 'nt':
            self._private(path)
        elif info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise KeyFailure('INSECURE_PERMISSIONS')

    def _read(self, path):
        self._permissions(path)
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            current = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_ino, info.st_dev) != (current.st_ino, current.st_dev):
                raise KeyFailure('UNSAFE_PATH')
            if info.st_size > self.MAX_READ:
                raise KeyFailure('VAULT_TOO_LARGE')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read(self.MAX_READ + 1)
            if len(data) > self.MAX_READ:
                raise KeyFailure('VAULT_TOO_LARGE')
            return data
        finally:
            os.close(fd)

    def _publish(self, name, data, owned):
        target = self.directory / name
        temp = self.directory / ('.vault-' + random_secrets.token_hex(16))
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
        owned[temp] = (os.fstat(fd).st_ino, os.fstat(fd).st_dev)
        try:
            self._private(temp)
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        # Windows CRT file descriptors do not permit unlink while open. Close
        # only after fsync, then publish without any overwrite-capable operation.
        _check_path(self.directory)
        os.link(temp, target, follow_symlinks=False)
        owned[target] = owned[temp]
        temp.unlink()
        del owned[temp]

    def _existing_state(self):
        for root, dirs, files in os.walk(self.directory, followlinks=False):
            for name in dirs + files:
                path = Path(root) / name
                if _unsafe(path):
                    raise KeyFailure('UNSAFE_PATH')
            for name in files:
                lower = name.lower()
                if lower.endswith(('.db', '.sqlite', '.sqlite3')):
                    raise KeyFailure('EXISTING_STATE')

    def initialize(self) -> Identity:
        self._platform()
        owned = {}
        try:
            _check_path(self.directory)
            if self.directory.exists():
                if not self.directory.is_dir():
                    raise KeyFailure('UNSAFE_PATH')
                if any((self.directory / name).exists() or (self.directory / name).is_symlink() for name in (self.VAULT, self.CERT, self.KEY)):
                    raise KeyFailure('VAULT_EXISTS')
                self._existing_state()
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._private(self.directory)
            secret = Secrets(os.urandom(32), os.urandom(32), random_secrets.token_urlsafe(32), random_secrets.token_urlsafe(32))
            key = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'EGO local core')])
            now = datetime.now(timezone.utc)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=365))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=None, decipher_only=None), critical=True)
                .sign(key, hashes.SHA256()))
            pin = certificate.fingerprint(hashes.SHA256()).hex()
            payload = json.dumps({'version': 1, 'main_key': base64.b64encode(secret.main_key).decode(), 'profile_key': base64.b64encode(secret.profile_key).decode(), 'admin_token': secret.admin_token, 'tls_password': secret.tls_password, 'pin': pin}, sort_keys=True).encode()
            try:
                protected = self._protect(payload)
                if not isinstance(protected, bytes) or not protected or len(protected) > self.MAX_READ:
                    raise ValueError()
            except Exception:
                raise KeyFailure('VAULT_PROTECT_FAILED') from None
            self._publish(self.CERT, certificate.public_bytes(serialization.Encoding.PEM), owned)
            self._publish(self.KEY, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(secret.tls_password.encode())), owned)
            self._publish(self.VAULT, protected, owned)
            if os.name != 'nt':
                fd = os.open(self.directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return Identity(secret, self.directory / self.CERT, self.directory / self.KEY, pin)
        except Exception as error:
            for path, expected in owned.items():
                try:
                    info = path.lstat()
                    if (info.st_ino, info.st_dev) == expected and not _unsafe(path):
                        path.unlink()
                except OSError:
                    pass
            if isinstance(error, KeyFailure):
                raise
            raise KeyFailure('VAULT_INITIALIZE_FAILED') from None

    def unlock(self) -> Identity:
        self._platform()
        try:
            _check_path(self.directory)
            if not self.directory.exists():
                raise KeyFailure('VAULT_MISSING')
            self._permissions(self.directory)
            paths = [self.directory / name for name in (self.VAULT, self.CERT, self.KEY)]
            if not all(path.exists() for path in paths):
                raise KeyFailure('VAULT_INCOMPLETE')
            wrapped, cert_data, key_data = [self._read(path) for path in paths]
            try:
                raw = self._unprotect(wrapped)
                if not isinstance(raw, bytes) or len(raw) > 16384:
                    raise ValueError()
            except Exception:
                raise KeyFailure('VAULT_UNPROTECT_FAILED') from None
            try:
                payload = json.loads(raw)
                if set(payload) != {'version', 'main_key', 'profile_key', 'admin_token', 'tls_password', 'pin'} or type(payload['version']) is not int or payload['version'] != 1:
                    raise ValueError()
                main = base64.b64decode(payload['main_key'], validate=True)
                profile = base64.b64decode(payload['profile_key'], validate=True)
                if len(main) != 32 or len(profile) != 32 or main == profile:
                    raise ValueError()
                for field in ('admin_token', 'tls_password'):
                    value = payload[field]
                    if not isinstance(value, str) or len(value) != 43 or len(base64.b64decode(value + '=', altchars=b'-_', validate=True)) != 32:
                        raise ValueError()
                secret = Secrets(main, profile, payload['admin_token'], payload['tls_password'])
            except Exception:
                raise KeyFailure('VAULT_INVALID') from None
            try:
                certificate = x509.load_pem_x509_certificate(cert_data)
                key = serialization.load_pem_private_key(key_data, secret.tls_password.encode())
                now = datetime.now(timezone.utc)
                if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
                    raise ValueError()
                if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
                    raise ValueError()
                public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                if public != certificate.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
                    raise ValueError()
                certificate.verify_directly_issued_by(certificate)
                san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                if san.get_values_for_type(x509.DNSName) != ['localhost'] or san.get_values_for_type(x509.IPAddress) != [ipaddress.ip_address('127.0.0.1')]:
                    raise ValueError()
                if list(certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != [ExtendedKeyUsageOID.SERVER_AUTH]:
                    raise ValueError()
                pin = certificate.fingerprint(hashes.SHA256()).hex()
                if not isinstance(payload['pin'], str) or not random_secrets.compare_digest(pin, payload['pin']):
                    raise ValueError()
            except Exception:
                raise KeyFailure('TLS_IDENTITY_INVALID') from None
            return Identity(secret, paths[1], paths[2], pin)
        except KeyFailure:
            raise
        except Exception:
            raise KeyFailure('VAULT_READ_FAILED') from None
