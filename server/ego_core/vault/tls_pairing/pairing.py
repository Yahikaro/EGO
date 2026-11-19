"""In-memory experiment, not persistent production device management."""
import hashlib
import hmac
import secrets
import threading
import time


class PairingFailure(ValueError):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).digest()


class PairingStore:
    def __init__(self, clock=time.monotonic, ttl=60, max_attempts=5):
        if not 1 <= ttl <= 300 or not 1 <= max_attempts <= 10:
            raise ValueError('Invalid pairing limits')
        self.clock, self.ttl, self.max_attempts = clock, ttl, max_attempts
        self.lock = threading.RLock()
        self.ticket = None
        self.pending = None
        self.devices = {}

    def issue(self):
        with self.lock:
            ticket, code = secrets.token_urlsafe(32), f'{secrets.randbelow(10**8):08d}'
            self.ticket = {'id': digest(ticket), 'code': digest(code),
                           'expiry': self.clock() + self.ttl, 'attempts': 0, 'used': False}
            self.pending = None
            return ticket, code

    def redeem(self, ticket, code):
        with self.lock:
            item = self.ticket
            if item is None or not hmac.compare_digest(item['id'], digest(ticket)):
                raise PairingFailure('INVALID_TICKET')
            if self.clock() >= item['expiry']:
                raise PairingFailure('EXPIRED')
            if item['used']:
                raise PairingFailure('REPLAY')
            if item['attempts'] >= self.max_attempts:
                raise PairingFailure('LOCKED')
            item['attempts'] += 1
            if not hmac.compare_digest(item['code'], digest(code)):
                raise PairingFailure('WRONG_CODE')
            item['used'] = True
            pending, secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.pending = {'id': digest(pending), 'secret': digest(secret),
                            'expiry': item['expiry'], 'approved': False}
            return pending, secret

    def _pending(self, pending):
        item = self.pending
        if item is None or not hmac.compare_digest(item['id'], digest(pending)):
            raise PairingFailure('INVALID_PENDING')
        if self.clock() >= item['expiry']:
            raise PairingFailure('EXPIRED')
        return item

    def approve(self, pending):
        with self.lock:
            self._pending(pending)['approved'] = True

    def claim(self, pending, secret):
        with self.lock:
            item = self._pending(pending)
            if not hmac.compare_digest(item['secret'], digest(secret)):
                raise PairingFailure('INVALID_SECRET')
            if not item['approved']:
                raise PairingFailure('APPROVAL_REQUIRED')
            if len(self.devices) >= 16:
                raise PairingFailure('DEVICE_LIMIT')
            device, token = secrets.token_urlsafe(16), secrets.token_urlsafe(32)
            self.devices[device] = digest(token)
            self.pending = None
            return device, token

    def authenticate(self, token):
        with self.lock:
            candidate = digest(token)
            for device, hashed in self.devices.items():
                if hmac.compare_digest(candidate, hashed):
                    return device
            raise PairingFailure('UNAUTHORIZED')

    def revoke(self, device):
        with self.lock:
            self.devices.pop(device, None)
