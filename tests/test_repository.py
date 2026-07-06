from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.context import Actor, TenantContext
from shared.errors import (
    InvariantViolationError,
    NotFoundError,
    PermissionDeniedError,
)
from shared.pagination import Page
from shared.repository import Repository
from tests.models import Gadget

pytestmark = pytest.mark.integration


class GadgetRepository(Repository[Gadget]):
    model = Gadget


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as sess:
        yield sess
        await sess.rollback()


async def seed(session: AsyncSession, ctx: TenantContext, *names: str) -> list[Gadget]:
    repo = GadgetRepository(session, ctx)
    return [await repo.add(Gadget(name=name)) for name in names]


async def test_tenant_isolation_test_gadgets(
    session: AsyncSession,
    tenant_ctx: TenantContext,
    other_tenant_ctx: TenantContext,
) -> None:
    """Mandatory tenant-isolation test for the test_gadgets table (DoD)."""
    (own,) = await seed(session, tenant_ctx, "own")
    (foreign,) = await seed(session, other_tenant_ctx, "foreign")

    repo = GadgetRepository(session, tenant_ctx)
    found = await repo.find()
    assert [gadget.id for gadget in found] == [own.id]

    # A foreign object is indistinguishable from a missing one (404, not 403).
    assert await repo.get(foreign.id) is None
    with pytest.raises(NotFoundError):
        await repo.get_or_raise(foreign.id)
    assert await repo.count() == 1


async def test_add_stamps_tenant_from_context(
    session: AsyncSession, tenant_ctx: TenantContext
) -> None:
    repo = GadgetRepository(session, tenant_ctx)
    gadget = await repo.add(Gadget(name="stamped"))
    assert gadget.tenant_id == tenant_ctx.tenant_id
    assert gadget.id is not None  # UUIDv7 assigned by the application before INSERT


async def test_add_rejects_foreign_tenant_stamp(
    session: AsyncSession, tenant_ctx: TenantContext
) -> None:
    repo = GadgetRepository(session, tenant_ctx)
    alien = Gadget(name="alien")
    alien.tenant_id = uuid4()
    with pytest.raises(PermissionDeniedError):
        await repo.add(alien)


async def test_delete_rejects_foreign_entity(
    session: AsyncSession,
    tenant_ctx: TenantContext,
    other_tenant_ctx: TenantContext,
) -> None:
    (foreign,) = await seed(session, other_tenant_ctx, "foreign")
    repo = GadgetRepository(session, tenant_ctx)
    with pytest.raises(PermissionDeniedError):
        await repo.delete(foreign)


async def test_find_paged_and_ordering(session: AsyncSession, tenant_ctx: TenantContext) -> None:
    await seed(session, tenant_ctx, "c", "a", "b")
    repo = GadgetRepository(session, tenant_ctx)
    page = await repo.find_paged(order_by=[Gadget.name], page=Page(limit=2, offset=0))
    assert [gadget.name for gadget in page.items] == ["a", "b"]
    assert page.total == 3
    assert (page.limit, page.offset) == (2, 0)


async def test_find_with_filter(session: AsyncSession, tenant_ctx: TenantContext) -> None:
    await seed(session, tenant_ctx, "alpha", "beta")
    repo = GadgetRepository(session, tenant_ctx)
    found = await repo.find(Gadget.name == "alpha")
    assert [gadget.name for gadget in found] == ["alpha"]


async def test_repository_requires_tenant_context(session: AsyncSession) -> None:
    userscope_ctx = TenantContext(tenant_id=None, actor=Actor(kind="user", id="u"), request_id=None)
    with pytest.raises(InvariantViolationError, match="tenant"):
        GadgetRepository(session, userscope_ctx)
