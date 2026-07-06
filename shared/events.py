"""In-process event bus with reliable (arq) delivery of marked handlers.

Contract (interfaces doc §2.6, ADR-0006):
- event names: ``<module>.<entity>.<action>`` (decision OV-09);
- publication happens post-commit only (``Service.emit`` + UnitOfWork hook);
- ``reliable=False`` — at-most-once, same process, errors never crash the
  publisher; ``reliable=True`` — enqueued to arq, at-least-once with
  deduplication via the ``processed_events`` table (see app/worker.py);
- wildcard/prefix subscriptions are a privilege of core modules (v1: audit).
"""

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from shared.context import Actor, ActorKind

logger = logging.getLogger(__name__)

_SEGMENT = r"[a-z][a-z0-9_]*"
EVENT_NAME_RE = re.compile(rf"^{_SEGMENT}\.{_SEGMENT}\.{_SEGMENT}$")
_PREFIX_PATTERN_RE = re.compile(rf"^{_SEGMENT}(\.{_SEGMENT})?\.\*$")

WILDCARD_ALLOWED_TOP_PACKAGE = "core"


def validate_event_name(name: str) -> None:
    if not EVENT_NAME_RE.match(name):
        raise ValueError(
            f"invalid event name {name!r}: expected '<module>.<entity>.<action>' "
            "in snake_case (e.g. 'billing.payment.succeeded')"
        )


def validate_pattern(pattern: str) -> None:
    if pattern == "*":
        return
    if _PREFIX_PATTERN_RE.match(pattern) or EVENT_NAME_RE.match(pattern):
        return
    raise ValueError(
        f"invalid subscription pattern {pattern!r}: expected exact event name, "
        "'<module>.*', '<module>.<entity>.*' or '*'"
    )


def pattern_matches(pattern: str, name: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return name.startswith(pattern[:-1])
    return pattern == name


def _is_wildcard(pattern: str) -> bool:
    return pattern == "*" or pattern.endswith(".*")


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: UUID  # dedup key; assigned at emit time
    name: str
    version: int  # payload schema version, starts at 1
    occurred_at: datetime  # UTC
    tenant_id: UUID | None  # None — platform events only
    actor: Actor
    payload: Mapping[str, Any]  # JSON types only; no secrets, ever

    def to_wire(self) -> dict[str, Any]:
        """JSON-safe representation: UUID -> str, datetime -> ISO 8601 (§2.6)."""
        return {
            "event_id": str(self.event_id),
            "name": self.name,
            "version": self.version,
            "occurred_at": self.occurred_at.isoformat(),
            "tenant_id": str(self.tenant_id) if self.tenant_id is not None else None,
            "actor": {"kind": self.actor.kind, "id": self.actor.id},
            "payload": dict(self.payload),
        }

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> "EventEnvelope":
        actor_data = data["actor"]
        kind: ActorKind = actor_data["kind"]
        tenant_raw = data["tenant_id"]
        return cls(
            event_id=UUID(data["event_id"]),
            name=data["name"],
            version=int(data["version"]),
            occurred_at=datetime.fromisoformat(data["occurred_at"]),
            tenant_id=UUID(tenant_raw) if tenant_raw is not None else None,
            actor=Actor(kind=kind, id=actor_data["id"]),
            payload=dict(data["payload"]),
        )


EventHandler = Callable[[EventEnvelope], Awaitable[None]]
# (handler_id, wire envelope) -> enqueue an arq job for reliable delivery.
ReliableEnqueue = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Subscription:
    pattern: str
    handler: EventHandler
    reliable: bool
    handler_id: str
    # Reliable core sinks that legitimately write system rows (tenant_id NULL)
    # run under app_maintenance; ordinary handlers run as app_user in the
    # envelope's tenant context (schema §3.4). v1: only the audit sink.
    maintenance: bool = False


class EventBus:
    """Registry + dispatcher. Subscriptions are registered at import time in
    every process (web and arq worker) — registration modules must be imported
    by both (interfaces doc §2.6, process topology)."""

    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []
        self._by_id: dict[str, Subscription] = {}
        self._enqueue: ReliableEnqueue | None = None

    def bind_enqueue(self, enqueue: ReliableEnqueue) -> None:
        """Bind the arq enqueue callable; done at startup by web and worker."""
        self._enqueue = enqueue

    def subscribe(
        self, pattern: str, *, reliable: bool = False, maintenance: bool = False
    ) -> Callable[[EventHandler], EventHandler]:
        validate_pattern(pattern)
        if maintenance and not reliable:
            raise ValueError("maintenance handlers must be reliable")

        def decorator(handler: EventHandler) -> EventHandler:
            if _is_wildcard(pattern) or maintenance:
                top_package = handler.__module__.split(".")[0]
                if top_package != WILDCARD_ALLOWED_TOP_PACKAGE:
                    raise RuntimeError(
                        f"wildcard/maintenance subscription {pattern!r} is a privilege of "
                        f"core modules; feature handlers subscribe to explicit event names "
                        f"as app_user (handler: {handler.__module__})"
                    )
            handler_id = f"{handler.__module__}.{handler.__qualname__}"
            if handler_id in self._by_id:
                raise RuntimeError(f"duplicate event handler registration: {handler_id}")
            subscription = Subscription(
                pattern=pattern,
                handler=handler,
                reliable=reliable,
                handler_id=handler_id,
                maintenance=maintenance,
            )
            self._subscriptions.append(subscription)
            self._by_id[handler_id] = subscription
            return handler

        return decorator

    def resolve(self, handler_id: str) -> Subscription:
        try:
            return self._by_id[handler_id]
        except KeyError:
            raise LookupError(
                f"event handler {handler_id!r} is not registered in this process; "
                "reliable handlers must be importable by the arq worker"
            ) from None

    def subscriptions_for(self, name: str) -> tuple[Subscription, ...]:
        return tuple(s for s in self._subscriptions if pattern_matches(s.pattern, name))

    async def publish(self, event: EventEnvelope) -> None:
        """Deliver post-commit. In-process handler errors are logged and never
        crash the publisher; reliable handlers are enqueued to arq."""
        validate_event_name(event.name)
        matching = self.subscriptions_for(event.name)
        if any(s.reliable for s in matching) and self._enqueue is None:
            raise RuntimeError(
                "EventBus has reliable subscribers but no enqueue bound; "
                "call bind_enqueue() at startup"
            )
        for subscription in matching:
            if subscription.reliable:
                assert self._enqueue is not None  # noqa: S101 - checked above
                try:
                    await self._enqueue(subscription.handler_id, event.to_wire())
                except Exception:
                    logger.exception(
                        "failed to enqueue reliable event handler",
                        extra={"event": event.name, "handler": subscription.handler_id},
                    )
            else:
                try:
                    await subscription.handler(event)
                except Exception:
                    logger.exception(
                        "in-process event handler failed",
                        extra={"event": event.name, "handler": subscription.handler_id},
                    )


# Process-global default bus. Core modules register their subscriptions on it
# at import time; the app and the arq worker bind the enqueue at startup.
bus = EventBus()
