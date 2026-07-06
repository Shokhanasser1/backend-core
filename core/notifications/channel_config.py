"""Channel config schemas + address masking (interfaces §3.4, threat model V10).

The set of channels and the required fields of each per-tenant config live here,
one source of truth the Phase 3 adapters align with. set_channel_config validates
against this before encrypting; get_channel_status never returns secrets. Log
masking of recipient addresses (phone/email) also lives here.
"""

from collections.abc import Mapping

from shared.errors import InvariantViolationError, NotFoundError

# channel code -> required config keys (values are secrets, stored encrypted).
CHANNEL_CONFIG_FIELDS: dict[str, frozenset[str]] = {
    "telegram": frozenset({"bot_token"}),
    "sms_eskiz": frozenset({"email", "password"}),
    "email": frozenset({"host", "port", "username", "password", "from_address"}),
}

KNOWN_CHANNELS: frozenset[str] = frozenset(CHANNEL_CONFIG_FIELDS)


def require_known_channel(channel: str) -> None:
    if channel not in KNOWN_CHANNELS:
        raise NotFoundError(f"unknown notification channel: {channel}")


def validate_channel_config(channel: str, config: Mapping[str, object]) -> None:
    """Every required field present and non-empty; unknown channel -> NotFoundError."""
    require_known_channel(channel)
    required = CHANNEL_CONFIG_FIELDS[channel]
    missing = {name for name in required if not config.get(name)}
    if missing:
        raise InvariantViolationError(
            f"channel {channel!r} config is missing required field(s): {sorted(missing)}"
        )


def mask_address(address: str) -> str:
    """Redact a recipient for logs: phone/email keep only edges (threat model)."""
    if "@" in address:
        local, _, domain = address.partition("@")
        head = local[:2] if len(local) > 2 else local[:1]
        return f"{head}***@{domain}"
    if len(address) <= 4:
        return "***"
    return f"{address[:4]}***{address[-2:]}"
