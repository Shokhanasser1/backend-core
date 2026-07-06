"""Application-level secret encryption (Fernet/MultiFernet; decision OV-19).

A cross-cutting primitive in shared/: TOTP secrets (core/auth) and tenant
channel configs (core/notifications) are both encrypted with a key from the
environment, so the cipher lives below both — keeping app -> core -> shared.
MultiFernet supports rotation: put the new key first, keep old keys for
decryption, re-encrypt lazily. In dev, an empty key list falls back to a fixed,
obviously-insecure key so the stack runs without configuration.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, MultiFernet


def _dev_key() -> str:
    # Deterministic, clearly-insecure dev key. Production supplies real keys.
    digest = hashlib.sha256(b"backend-core-dev-encryption-key").digest()
    return base64.urlsafe_b64encode(digest).decode()


class SecretCipher:
    def __init__(self, keys: tuple[str, ...]) -> None:
        key_list = list(keys) if keys else [_dev_key()]
        self._fernet = MultiFernet([Fernet(k.encode()) for k in key_list])

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode()

    def rotate(self, token: bytes) -> bytes:
        """Re-encrypt an existing token under the current primary key."""
        return self._fernet.rotate(token)


def generate_key() -> str:
    """Generate a fresh Fernet key (for docs/ops: create SECRET_ENCRYPTION_KEYS)."""
    return Fernet.generate_key().decode()
