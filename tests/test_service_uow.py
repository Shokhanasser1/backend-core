import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.context import TenantContext
from shared.events import EventBus, EventEnvelope
from shared.service import Service, SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration


@pytest.fixture
def recording_bus() -> tuple[EventBus, list[EventEnvelope]]:
    bus = EventBus()
    received: list[EventEnvelope] = []

    @bus.subscribe("testmod.gadget.created")
    async def record(event: EventEnvelope) -> None:
        received.append(event)

    return bus, received


async def test_events_published_only_after_commit(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
    recording_bus: tuple[EventBus, list[EventEnvelope]],
) -> None:
    bus, received = recording_bus
    uow = SqlAlchemyUnitOfWork(session_factory)
    async with uow:
        service = Service(uow, bus, tenant_ctx)
        event_id = service.emit("testmod.gadget.created", {"gadget_id": "g-1"})
        # Inside the transaction nothing is published yet.
        assert received == []
    assert [e.event_id for e in received] == [event_id]
    envelope = received[0]
    assert envelope.tenant_id == tenant_ctx.tenant_id
    assert envelope.actor == tenant_ctx.actor
    assert envelope.payload == {"gadget_id": "g-1"}
    assert envelope.version == 1


async def test_rollback_discards_events(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
    recording_bus: tuple[EventBus, list[EventEnvelope]],
) -> None:
    bus, received = recording_bus
    uow = SqlAlchemyUnitOfWork(session_factory)
    with pytest.raises(RuntimeError, match="business failure"):
        async with uow:
            service = Service(uow, bus, tenant_ctx)
            service.emit("testmod.gadget.created", {"gadget_id": "g-2"})
            raise RuntimeError("business failure")
    # Rollback discards pending events — no ghost events.
    assert received == []


async def test_multiple_events_preserve_order(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
    recording_bus: tuple[EventBus, list[EventEnvelope]],
) -> None:
    bus, received = recording_bus
    uow = SqlAlchemyUnitOfWork(session_factory)
    async with uow:
        service = Service(uow, bus, tenant_ctx)
        first = service.emit("testmod.gadget.created", {"n": 1})
        second = service.emit("testmod.gadget.created", {"n": 2})
    assert [e.event_id for e in received] == [first, second]


async def test_emit_validates_event_name(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    async with uow:
        service = Service(uow, EventBus(), tenant_ctx)
        with pytest.raises(ValueError, match="invalid event name"):
            service.emit("gadget.created", {})
