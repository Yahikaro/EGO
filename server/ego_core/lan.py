"""Home-network access (stage 4i).

Off by default. When enabled in settings\\network.json, Core adds a second
listener: HTTPS only, on the LAN port, with its own self-signed certificate for
this PC's private addresses. The loopback API (HTTP on the PC) is unchanged.
Requests that arrive from another device may only use an EGO account session
(see api.py); launch codes, admin and lifecycle routes stay loopback-only.
"""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_PORT = 18444
CERT, KEY, STATUS = 'lan.crt.pem', 'lan.key.pem', 'lan-status.json'
VALID_DAYS = 397
RENEW_BEFORE_DAYS = 30


def settings_path(root):
    return Path(root) / 'settings' / 'network.json'


def load_settings(root):
    """{'lan_access': bool, 'lan_port': int}; anything unreadable means 'off'."""
    result = {'lan_access': False, 'lan_port': DEFAULT_PORT}
    path = settings_path(root)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return result
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return result
    if isinstance(data, dict):
        if type(data.get('lan_access')) is bool:
            result['lan_access'] = data['lan_access']
        if type(data.get('lan_port')) is int and 1024 <= data['lan_port'] <= 65535:
            result['lan_port'] = data['lan_port']
    return result


def save_settings(root, *, lan_access, lan_port=DEFAULT_PORT):
    if type(lan_access) is not bool or type(lan_port) is not int or not 1024 <= lan_port <= 65535:
        raise ValueError('INVALID_NETWORK_SETTINGS')
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.network-' + secrets.token_hex(8))
    temp.write_text(json.dumps({'lan_access': lan_access, 'lan_port': lan_port}, indent=2), encoding='utf-8')
    os.replace(temp, path)
    return load_settings(root)


def private_addresses():
    """This PC's private IPv4 addresses (home network), best first."""
    found = []
    try:
        # No packet is sent: connect() on UDP only selects the outgoing interface.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.168.255.255', 9))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    result = []
    for value in found:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and address.is_private and not address.is_loopback and not address.is_link_local \
                and value not in result:
            result.append(value)
    return result


def _hostname():
    name = socket.gethostname().strip().lower()
    return name if name and all(c.isalnum() or c == '-' for c in name) else None


def _wanted_names(addresses):
    host = _hostname()
    dns = ['localhost'] + ([host, host + '.local'] if host else [])
    return dns, ['127.0.0.1', *addresses]


def _current(directory, password, dns, ips):
    """Existing certificate if it is ours, valid long enough and covers the addresses."""
    cert_path, key_path = directory / CERT, directory / KEY
    if cert_path.is_symlink() or key_path.is_symlink() or not cert_path.is_file() or not key_path.is_file():
        return None
    try:
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password.encode())
        if key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != \
                certificate.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
            return None
        if certificate.not_valid_after_utc - datetime.now(timezone.utc) < timedelta(days=RENEW_BEFORE_DAYS):
            return None
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have_ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
        have_dns = set(san.get_values_for_type(x509.DNSName))
        if not set(ips) <= have_ips or not set(dns) <= have_dns:
            return None
        return certificate
    except Exception:
        return None


def _write_private(path, data):
    from server.ego_core.keys import _privatize
    temp = path.with_name('.lan-' + secrets.token_hex(8))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        _privatize(temp)
    except Exception:
        pass
    os.replace(temp, path)


def ensure_certificate(directory, password, addresses):
    """(certfile, keyfile, sha256 fingerprint) for the LAN listener; reuses a valid one."""
    directory = Path(directory)
    dns, ips = _wanted_names(addresses)
    certificate = _current(directory, password, dns, ips)
    if certificate is None:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'EGO home network'),
                          x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'EGO local')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in dns] +
                                                       [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
        _write_private(directory / KEY, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.BestAvailableEncryption(password.encode())))
        _write_private(directory / CERT, certificate.public_bytes(serialization.Encoding.PEM))
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex().upper()
    return directory / CERT, directory / KEY, ':'.join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2))


def write_status(root, status):
    """Public (secret-free) facts for the Control Center and the client."""
    path = Path(root) / 'data' / STATUS
    temp = path.with_name('.lan-status-' + secrets.token_hex(8))
    temp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def read_status(root):
    try:
        data = json.loads((Path(root) / 'data' / STATUS).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def urls(status):
    if not status or not status.get('enabled'):
        return []
    return [f"https://{address}:{status['port']}/app/" for address in status.get('addresses', [])]
