"""Payment state machine (schema §2.3; interfaces §4.1).

Owned by billing, not the adapter. Transitions go forward only:
created -> pending -> succeeded | failed | canceled | expired
(created may also go straight to a terminal state, e.g. cancel of an unopened
checkout). Refunds are out of v1 scope (OV-22): succeeded is terminal.

``expired`` is OUR abandonment marker (checkout TTL), not the provider's verdict:
a provider may still confirm the payment after it (a Stripe Checkout Session
outlives our TTL; a Payme/Click transaction opened before it may be performed
after it). Money has moved by then, so ``expired -> succeeded`` is allowed — a late
success is honored, never dropped.
"""

from shared.errors import InvariantViolationError

TERMINAL_STATES = frozenset({"succeeded", "failed", "canceled", "expired"})

_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"pending", "succeeded", "failed", "canceled", "expired"}),
    "pending": frozenset({"succeeded", "failed", "canceled", "expired"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
    "expired": frozenset({"succeeded"}),  # late provider success (see module doc)
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
