"""Loopback test client: PKI + hostname validation, then pin, then HTTP."""
import hmac
import http.client
import json
import ssl

from cryptography import x509
from server.ego_core.vault.tls_pairing.certificates import spki_pin


class PinMismatch(ValueError):
    pass


class PinnedClient:
    def __init__(self, ca_pem, pin, port):
        self.context = ssl.create_default_context(cadata=ca_pem)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.pin = pin
        self.port = port

    def request(self, method, path, payload=None, token=None):
        connection = http.client.HTTPSConnection('127.0.0.1', self.port,
                                                 context=self.context, timeout=3)
        try:
            connection.connect()
            cert = x509.load_der_x509_certificate(connection.sock.getpeercert(binary_form=True))
            if not hmac.compare_digest(spki_pin(cert), self.pin):
                raise PinMismatch('SPKI_MISMATCH')
            headers = {'Content-Type': 'application/json'}
            if token is not None:
                headers['Authorization'] = 'Bearer ' + token
            connection.request(method, path, body=json.dumps(payload) if payload is not None else None,
                               headers=headers)
            response = connection.getresponse()
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('RESPONSE_TOO_LARGE')
            return response.status, json.loads(raw)
        finally:
            connection.close()
