"""Opaque token helpers shared across core modules.

Random URL-safe tokens (refresh, password reset, 2FA challenge, invitation,
email verification) with SHA-256 hashing — only the hash is ever stored. Kept in
shared so both core/auth and core/tenants use them without a horizontal import.
"""

import hashlib
import secrets


def new_opaque_token() -> str:
    """A 256-bit URL-safe random token."""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """SHA-256 hex of an opaque token; the raw value is never stored."""
    return hashlib.sha256(raw.encode()).hexdigest()
