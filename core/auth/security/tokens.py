"""JWT access tokens + opaque token helpers (threat model V3; decisions OV-17, OV-03).

Access tokens are short-lived JWTs signed with HS256 (OV-17) carrying the
tenant claim (OV-03): the token is issued for a user-tenant pair, or user-scope
(tenant None) before a tenant is chosen. Verification pins exactly one algorithm
from config — the ``alg`` header of the token never selects it — which closes
the alg-confusion / ``alg:none`` class (V3).

Refresh, password-reset, 2FA-challenge, invitation and email-verification tokens
are opaque random strings; only their SHA-256 hash is ever stored.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from shared.config import Settings
from shared.errors import AuthenticationError
from shared.ids import new_uuid7
from shared.security_tokens import hash_token, new_opaque_token

__all__ = [
    "ACCESS_TOKEN_TYPE",
    "decode_access_token",
    "encode_access_token",
    "hash_token",
    "new_opaque_token",
]

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 - token type label, not a secret


def encode_access_token(
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, UUID]:
    """Return (jwt, jti). jti is returned so callers can correlate/audit."""
    issued_at = now or datetime.now(UTC)
    jti = new_uuid7()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant": str(tenant_id) if tenant_id is not None else None,
        "type": ACCESS_TOKEN_TYPE,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
        "jti": str(jti),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify with exactly one allowed algorithm from config. Any failure —
    bad signature, expiry, wrong/none algorithm — raises AuthenticationError."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],  # strict allowlist of one (V3)
            options={"require": ["exp", "iat", "sub", "type"]},
        )
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("invalid access token") from exc
    if claims.get("type") != ACCESS_TOKEN_TYPE:
        raise AuthenticationError("wrong token type")
    return claims
