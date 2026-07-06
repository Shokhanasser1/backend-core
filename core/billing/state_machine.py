"""Payment state machine (schema §2.3; interfaces §4.1).

Owned by billing, not the adapter. Transitions go forward only:
created -> pending -> succeeded | failed | canceled | expired
(created may also go straight to a terminal state, e.g. cancel of an unopened
checkout). Refunds are out of v1 scope (OV-22): succeeded is terminal.
"""

from shared.errors import InvariantViolationError

TERMINAL_STATES = frozenset({"succeeded", "failed", "canceled", "expired"})

_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"pending", "succeeded", "failed", "canceled", "expired"}),
    "pending": frozenset({"succeeded", "failed", "canceled", "expired"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
    "expired": frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    """Raise InvariantViolationError for a disallowed transition (the caller
    maps it to the provider dialect at the webhook boundary)."""
    if not can_transition(current, target):
        raise InvariantViolationError(f"invalid payment transition: {current} -> {target}")


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATES
