"""Reliable delivery via the arq dispatcher: processed_events deduplication,
retries with backoff, dead letter (interfaces doc §2.6)."""

from typing import Any

import pytest
from arq.worker import Retry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.worker import MAX_TRIES, dispatch_event
from shared.events import EventBus, EventEnvelope
from shared.processed_events import ProcessedEvent
from tests.test_events_bus import make_envelope

pytestmark = pytest.mark.integration


def make_worker_ctx(
    session_factory: async_sessionmaker[AsyncSession], bus: EventBus, job_try: int = 1
) -> dict[str, Any]:
    return {"bus": bus, "session_factory": session_factory, "job_try": job_try}


async def count_processed(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(ProcessedEvent))
        return result.scalar_one()


async def test_duplicate_delivery_runs_handler_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bus = EventBus()
    calls: list[str] = []

    @bus.subscribe("billing.payment.succeeded", reliable=True)
    async def handler(event: EventEnvelope) -> None:
        calls.append(event.name)

    envelope = make_envelope()
    wire = envelope.to_wire()
    handler_id = bus.subscriptions_for(envelope.name)[0].handler_id
    ctx = make_worker_ctx(session_factory, bus)

    await dispatch_event(ctx, handler_id, wire)
    await dispatch_event(ctx, handler_id, wire)  # repeated arq delivery

    assert calls == ["billing.payment.succeeded"]
    assert await count_processed(session_factory) == 1


async def test_failed_handler_is_retried_and_dedup_rolled_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bus = EventBus()

    @bus.subscribe("billing.payment.succeeded", reliable=True)
    async def handler(_event: EventEnvelope) -> None:
        raise RuntimeError("transient failure")

    envelope = make_envelope()
    handler_id = bus.subscriptions_for(envelope.name)[0].handler_id

    with pytest.raises(Retry):
        await dispatch_event(make_worker_ctx(session_factory, bus), handler_id, envelope.to_wire())
    # The handler's transaction rolled back together with the dedup row —
    # the retry will be able to process the event again.
    assert await count_processed(session_factory) == 0


async def test_final_failure_is_dead_lettered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bus = EventBus()

    @bus.subscribe("billing.payment.succeeded", reliable=True)
    async def handler(_event: EventEnvelope) -> None:
        raise RuntimeError("permanent failure")

    envelope = make_envelope()
    handler_id = bus.subscriptions_for(envelope.name)[0].handler_id
    ctx = make_worker_ctx(session_factory, bus, job_try=MAX_TRIES)

    with pytest.raises(RuntimeError, match="permanent failure"):
        await dispatch_event(ctx, handler_id, envelope.to_wire())


async def test_unknown_handler_is_not_retried(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bus = EventBus()
    ctx = make_worker_ctx(session_factory, bus)
    with pytest.raises(LookupError, match="not registered"):
        await dispatch_event(ctx, "ghost.module.handler", make_envelope().to_wire())
