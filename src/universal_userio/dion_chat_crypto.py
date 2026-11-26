"""DION chat wire encryption, observed in web-client asset 6173.a3920354.js.

Ephemeral P-256 ECDH -> HKDF-SHA256 (empty salt/info) -> AES-256-GCM.
This is independent of the backend-owned access/refresh credential chain.
"""
import base64
import binascii
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class DionChatCrypto:
    def __init__(self):
        self._private = ec.generate_private_key(ec.SECP256R1())
        self._cipher = None

    @property
    def public_key(self) -> str:
        raw = self._private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return base64.b64encode(raw).decode('ascii')

    def establish(self, server_public_key: str) -> None:
        if self._cipher is not None:
            raise ValueError('chat crypto session already established')
        try:
            raw = base64.b64decode(server_public_key, validate=True)
            public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
            shared = self._private.exchange(ec.ECDH(), public)
            key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b'', info=b'').derive(shared)
        except (ValueError, TypeError, binascii.Error):
            raise ValueError('invalid DION chat public key') from None
        self._cipher = AESGCM(key)

    def encrypt_json(self, payload) -> str:
        if self._cipher is None:
            raise ValueError('chat crypto handshake required')
        nonce = os.urandom(12)
        plain = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        encrypted = self._cipher.encrypt(nonce, plain, None)
        # Twelve nonce bytes occupy exactly sixteen base64 characters (no pad).
        return base64.b64encode(nonce + encrypted).decode('ascii')

    def decrypt_json(self, encrypted: str):
        if self._cipher is None:
            raise ValueError('chat crypto handshake required')
        try:
            raw = base64.b64decode(encrypted, validate=True)
            if len(raw) < 28:
                raise ValueError('short envelope')
            plain = self._cipher.decrypt(raw[:12], raw[12:], None)
            return json.loads(plain)
        except (InvalidTag, ValueError, TypeError, binascii.Error, UnicodeError):
            raise ValueError('invalid DION encrypted chat response') from None
