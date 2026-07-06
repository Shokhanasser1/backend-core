"""Repository behaviour under the two lines of tenant isolation: the in-code
tenant filter AND Postgres RLS (app_user connection)."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.context import Actor, TenantContext
from shared.errors import InvariantViolationError, NotFoundError, PermissionDeniedError
from shared.pagination import Page
from shared.repository import Repository
from shared.service import SqlAlchemyUnitOfWork
from tests.models import Gadget

pytestmark = pytest.mark.integration


class GadgetRepository(Repository[Gadget]):
    model = Gadget


async def seed(
    factory: async_sessionmaker[AsyncSession], ctx: TenantContext, *names: str
) -> list[Gadget]:
    created: list[Gadget] = []
    async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
        repo = GadgetRepository(uow.session, ctx)
        for name in names:
            created.append(await repo.add(Gadget(name=name)))
    return created


async def test_tenant_isolation_test_gadgets(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
    other_tenant_ctx: TenantContext,
) -> None:
    """Mandatory tenant-isolation test for the test_gadgets table (DoD)."""
    (own,) = await seed(session_factory, tenant_ctx, "own")
    (foreign,) = await seed(session_factory, other_tenant_ctx, "foreign")

    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        repo = GadgetRepository(uow.session, tenant_ctx)
        found = await repo.find()
        assert [g.id for g in found] == [own.id]
        # A foreign object is indistinguishable from a missing one (404, not 403).
        assert await repo.get(foreign.id) is None
        with pytest.raises(NotFoundError):
            await repo.get_or_raise(foreign.id)
        assert await repo.count() == 1


async def test_add_stamps_tenant_from_context(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    (gadget,) = await seed(session_factory, tenant_ctx, "stamped")
    assert gadget.tenant_id == tenant_ctx.tenant_id
    assert gadget.id is not None  # UUIDv7 assigned by the application before INSERT


async def test_add_rejects_foreign_tenant_stamp(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        repo = GadgetRepository(uow.session, tenant_ctx)
        alien = Gadget(name="alien")
        alien.tenant_id = uuid4()
        with pytest.raises(PermissionDeniedError):
            await repo.add(alien)


async def test_find_paged_and_ordering(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    await seed(session_factory, tenant_ctx, "c", "a", "b")
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        repo = GadgetRepository(uow.session, tenant_ctx)
        page = await repo.find_paged(order_by=[Gadget.name], page=Page(limit=2, offset=0))
    assert [g.name for g in page.items] == ["a", "b"]
    assert page.total == 3
    assert (page.limit, page.offset) == (2, 0)


async def test_repository_requires_tenant_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    userscope_ctx = TenantContext(tenant_id=None, actor=Actor(kind="user", id="u"), request_id=None)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        with pytest.raises(InvariantViolationError, match="tenant"):
            GadgetRepository(uow.session, userscope_ctx)
