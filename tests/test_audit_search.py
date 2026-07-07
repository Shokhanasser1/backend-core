"""AuditService.search — filters, pagination, tenant isolation (interfaces §3.5).

Integration tests against a real Postgres so RLS is genuinely exercised: rows are
written and read as app_user in a tenant context, and a tenant admin must never
see another tenant's rows nor system (tenant_id NULL) rows.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.audit.models import AuditLog
from core.audit.schemas import AuditQuery, AuditRecordDTO
from core.audit.service import AuditService
from shared.context import Actor, TenantContext
from shared.ids import new_uuid7
from shared.pagination import Page
from shared.service import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration


async def _add(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    action: str,
    user_id: UUID | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    created_at: datetime | None = None,
) -> None:
    session.add(
        AuditLog(
            id=new_uuid7(),
            tenant_id=ctx.tenant_id,
            user_id=user_id or uuid4(),
            action=action,
            object_type=object_type,
            object_id=object_id,
            payload={},
            created_at=created_at or datetime.now(UTC),
        )
    )
    await session.flush()


async def _search(
    factory: async_sessionmaker[AsyncSession], ctx: TenantContext, query: AuditQuery, page: Page
) -> tuple[list[AuditRecordDTO], int]:
    async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
        result = await AuditService(uow.session, ctx).search(query, page)
        return list(result.items), result.total


async def test_action_prefix_filter(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        await _add(uow.session, tenant_ctx, action="auth.user.login_failed")
        await _add(uow.session, tenant_ctx, action="auth.user.password_changed")
        await _add(uow.session, tenant_ctx, action="billing.payment.succeeded")

    items, total = await _search(
        session_factory, tenant_ctx, AuditQuery(action_prefix="auth."), Page()
    )
    assert total == 2
    assert {i.action for i in items} == {"auth.user.login_failed", "auth.user.password_changed"}


async def test_actor_and_object_filters(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    actor = uuid4()
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        await _add(
            uow.session,
            tenant_ctx,
            action="a",
            user_id=actor,
            object_type="payment",
            object_id="p1",
        )
        await _add(uow.session, tenant_ctx, action="b", object_type="payment", object_id="p2")

    by_actor, actor_total = await _search(
        session_factory, tenant_ctx, AuditQuery(actor_user_id=actor), Page()
    )
    assert actor_total == 1
    assert by_actor[0].action == "a"

    by_object, object_total = await _search(
        session_factory,
        tenant_ctx,
        AuditQuery(object_type="payment", object_id="p2"),
        Page(),
    )
    assert object_total == 1
    assert by_object[0].action == "b"


async def test_date_range_filter(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        await _add(
            uow.session, tenant_ctx, action="old", created_at=datetime(2025, 1, 1, tzinfo=UTC)
        )
        await _add(
            uow.session, tenant_ctx, action="new", created_at=datetime(2026, 6, 1, tzinfo=UTC)
        )

    items, total = await _search(
        session_factory,
        tenant_ctx,
        AuditQuery(date_from=datetime(2026, 1, 1, tzinfo=UTC)),
        Page(),
    )
    assert total == 1
    assert items[0].action == "new"


async def test_pagination_and_ordering(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        for i in range(5):
            await _add(
                uow.session,
                tenant_ctx,
                action=f"e{i}",
                created_at=datetime(2026, 1, 1 + i, tzinfo=UTC),
            )

    page1, total = await _search(session_factory, tenant_ctx, AuditQuery(), Page(limit=2, offset=0))
    assert total == 5
    # Newest first.
    assert [i.action for i in page1] == ["e4", "e3"]
    page3, _ = await _search(session_factory, tenant_ctx, AuditQuery(), Page(limit=2, offset=4))
    assert [i.action for i in page3] == ["e0"]


async def test_tenant_isolation_excludes_other_and_system_rows(
    session_factory: async_sessionmaker[AsyncSession],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
    other_tenant_ctx: TenantContext,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        await _add(uow.session, tenant_ctx, action="mine")
    async with SqlAlchemyUnitOfWork(session_factory, context=other_tenant_ctx) as uow:
        await _add(uow.session, other_tenant_ctx, action="theirs")
    # A system row (tenant_id NULL) written by the maintenance sink.
    system_ctx = TenantContext(tenant_id=None, actor=Actor(kind="system", id=None), request_id=None)
    async with SqlAlchemyUnitOfWork(maintenance_session_factory, context=system_ctx) as uow:
        uow.session.add(AuditLog(id=new_uuid7(), tenant_id=None, action="platform", payload={}))
        await uow.session.flush()

    items, total = await _search(session_factory, tenant_ctx, AuditQuery(), Page())
    assert total == 1
    assert items[0].action == "mine"
