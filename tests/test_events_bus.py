from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from shared.context import Actor
from shared.events import (
    EventBus,
    EventEnvelope,
    pattern_matches,
    validate_event_name,
    validate_pattern,
)
from shared.ids import new_uuid7


def make_envelope(name: str = "billing.payment.succeeded") -> EventEnvelope:
    return EventEnvelope(
        event_id=new_uuid7(),
        name=name,
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=uuid4(),
        actor=Actor(kind="integration", id="payme"),
        payload={"amount": 100_000, "currency": "UZS"},
    )


class TestNamingConvention:
    @pytest.mark.parametrize(
        "name",
        ["billing.payment.succeeded", "commerce.order.created", "auth.user.two_factor_enabled"],
    )
    def test_valid_names(self, name: str) -> None:
        validate_event_name(name)

    @pytest.mark.parametrize(
        "name",
        ["order.created", "billing", "Billing.Payment.Succeeded", "billing..x", "a.b.c.d"],
    )
    def test_invalid_names(self, name: str) -> None:
        with pytest.raises(ValueError, match="invalid event name"):
            validate_event_name(name)

    @pytest.mark.parametrize("pattern", ["*", "billing.*", "billing.payment.*", "a.b.c"])
    def test_valid_patterns(self, pattern: str) -> None:
        validate_pattern(pattern)

    @pytest.mark.parametrize("pattern", ["", "*.created", "billing.payment.succeeded.*"])
    def test_invalid_patterns(self, pattern: str) -> None:
        with pytest.raises(ValueError, match="invalid subscription pattern"):
            validate_pattern(pattern)

    def test_matching(self) -> None:
        assert pattern_matches("*", "billing.payment.succeeded")
        assert pattern_matches("billing.*", "billing.payment.succeeded")
        assert pattern_matches("billing.payment.*", "billing.payment.succeeded")
        assert not pattern_matches("billing.payment.*", "billing.subscription.activated")
        assert not pattern_matches("commerce.*", "billing.payment.succeeded")
        assert pattern_matches("a.b.c", "a.b.c")


def test_envelope_wire_roundtrip() -> None:
    envelope = make_envelope()
    restored = EventEnvelope.from_wire(envelope.to_wire())
    assert restored == envelope


def test_platform_envelope_without_tenant_roundtrip() -> None:
    envelope = EventEnvelope(
        event_id=new_uuid7(),
        name="auth.user.registered",
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=None,
        actor=Actor(kind="system", id=None),
        payload={},
    )
    assert EventEnvelope.from_wire(envelope.to_wire()) == envelope


class TestSubscribe:
    def test_wildcard_forbidden_outside_core(self) -> None:
        bus = EventBus()
        with pytest.raises(RuntimeError, match="privilege of core modules"):

            @bus.subscribe("*")
            async def handler(_event: EventEnvelope) -> None: ...

    def test_wildcard_allowed_for_core(self) -> None:
        bus = EventBus()

        async def audit_sink(_event: EventEnvelope) -> None: ...

        audit_sink.__module__ = "core.audit.subscribers"
        bus.subscribe("*", reliable=True)(audit_sink)
        assert bus.subscriptions_for("billing.payment.succeeded")

    def test_duplicate_handler_rejected(self) -> None:
        bus = EventBus()

        async def handler(_event: EventEnvelope) -> None: ...

        bus.subscribe("billing.payment.succeeded")(handler)
        with pytest.raises(RuntimeError, match="duplicate"):
            bus.subscribe("billing.payment.failed")(handler)

    def test_resolve_unknown_handler(self) -> None:
        bus = EventBus()
        with pytest.raises(LookupError, match="not registered"):
            bus.resolve("nowhere.handler")


class TestPublish:
    async def test_in_process_delivery_and_error_isolation(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        @bus.subscribe("billing.payment.succeeded")
        async def broken(_event: EventEnvelope) -> None:
            raise RuntimeError("boom")

        async def watcher(_event: EventEnvelope) -> None:
            calls.append("watcher")

        # Prefix subscriptions are a privilege of core modules.
        watcher.__module__ = "core.audit.subscribers"
        bus.subscribe("billing.*")(watcher)

        # The first handler's error neither crashes the publisher nor blocks the second.
        await bus.publish(make_envelope())
        assert calls == ["watcher"]

    async def test_reliable_requires_bound_enqueue(self) -> None:
        bus = EventBus()

        @bus.subscribe("billing.payment.succeeded", reliable=True)
        async def handler(_event: EventEnvelope) -> None: ...

        with pytest.raises(RuntimeError, match="bind_enqueue"):
            await bus.publish(make_envelope())

    async def test_reliable_enqueues_wire_envelope(self) -> None:
        bus = EventBus()
        enqueued: list[tuple[str, dict[str, Any]]] = []

        async def fake_enqueue(handler_id: str, wire: dict[str, Any]) -> None:
            enqueued.append((handler_id, wire))

        bus.bind_enqueue(fake_enqueue)

        @bus.subscribe("billing.payment.succeeded", reliable=True)
        async def handler(_event: EventEnvelope) -> None:
            raise AssertionError("reliable handler must not run in-process")

        envelope = make_envelope()
        await bus.publish(envelope)
        assert len(enqueued) == 1
        handler_id, wire = enqueued[0]
        assert handler_id.endswith("handler")
        assert EventEnvelope.from_wire(wire) == envelope
