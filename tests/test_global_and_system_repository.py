"""GlobalRepository (global tables, no tenant filter) and SystemRepository
(read-only, core-only import contract)."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.errors import NotFoundError
from shared.ids import new_uuid7
from shared.processed_events import ProcessedEvent
from shared.repository import GlobalRepository
from shared.system_repository import SystemRepository
from tests.models import Gadget

pytestmark = pytest.mark.integration


class ProcessedEventGlobalRepository(GlobalRepository[ProcessedEvent]):
    model = ProcessedEvent


class ProcessedEventSystemRepository(SystemRepository[ProcessedEvent]):
    model = ProcessedEvent


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as sess:
        yield sess
        await sess.rollback()


async def test_global_repository_read_write(session: AsyncSession) -> None:
    """processed_events is a global service table; app_user has SELECT/INSERT
    only (schema §2.7), which is exactly what the dispatcher needs."""
    repo = ProcessedEventGlobalRepository(session)
    event_id = new_uuid7()
    row = await repo.add(ProcessedEvent(handler="core.audit.sink", event_id=event_id))
    assert row.processed_at is not None

    assert await repo.count() == 1
    fetched = await repo.get(("core.audit.sink", event_id))
    assert fetched is not None and fetched.event_id == event_id

    found = await repo.find(ProcessedEvent.handler == "core.audit.sink")
    assert len(found) == 1

    with pytest.raises(NotFoundError):
        await repo.get_or_raise(("ghost", new_uuid7()))


async def test_app_user_cannot_delete_processed_events(session: AsyncSession) -> None:
    """DELETE on processed_events is reserved for app_maintenance (§2.7); the
    grant denies it to app_user."""
    from sqlalchemy.exc import ProgrammingError

    repo = ProcessedEventGlobalRepository(session)
    row = await repo.add(ProcessedEvent(handler="core.audit.sink", event_id=new_uuid7()))
    await repo.delete(row)
    with pytest.raises(ProgrammingError, match="permission denied"):
        await session.flush()


async def test_global_repository_rejects_tenant_scoped_model(session: AsyncSession) -> None:
    class Broken(GlobalRepository[Gadget]):  # type: ignore[type-var]
        model = Gadget

    with pytest.raises(TypeError, match="tenant-scoped"):
        Broken(session)


async def test_system_repository_is_read_only_surface(session: AsyncSession) -> None:
    global_repo = ProcessedEventGlobalRepository(session)
    first = new_uuid7()
    await global_repo.add(ProcessedEvent(handler="core.billing.match", event_id=first))
    await global_repo.add(ProcessedEvent(handler="core.billing.match", event_id=new_uuid7()))

    system_repo = ProcessedEventSystemRepository(session)
    assert await system_repo.count() == 2

    one = await system_repo.get_one_or_none(ProcessedEvent.event_id == first)
    assert one is not None and one.event_id == first
    assert await system_repo.get_one_or_none(ProcessedEvent.event_id == new_uuid7()) is None

    with pytest.raises(ValueError, match="matched"):
        await system_repo.get_one_or_none(ProcessedEvent.handler == "core.billing.match")

    limited = await system_repo.find(
        ProcessedEvent.handler == "core.billing.match",
        order_by=[ProcessedEvent.event_id],
        limit=1,
    )
    assert len(limited) == 1

    # No write methods exist — the read-only guarantee is the interface shape.
    assert not hasattr(system_repo, "add")
    assert not hasattr(system_repo, "delete")
