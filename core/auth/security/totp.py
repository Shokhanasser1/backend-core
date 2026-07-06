"""TOTP 2FA + recovery codes (schema §2.1; decision OV-14).

TOTP secrets are generated here and encrypted by the caller (AuthService) via
SecretCipher before storage. Recovery codes are one-time; only their SHA-256
hash is stored (>= 64 bits of entropy, so a slow hash is unnecessary).
"""

import hashlib
import secrets

import pyotp

TOTP_ISSUER = "backend-core"
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I
_RECOVERY_LENGTH = 12  # ~59 bits of entropy


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_name: str) -> str:
    """otpauth:// URI for a QR code; the client renders it."""
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=TOTP_ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    # valid_window=1 tolerates one 30s step of clock skew.
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def generate_recovery_codes() -> list[str]:
    return [
        "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LENGTH))
        for _ in range(RECOVERY_CODE_COUNT)
    ]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()
