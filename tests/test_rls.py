"""RLS as the independent second line of tenant isolation (threat model V1).

These tests connect as app_user (non-owner, NOBYPASSRLS) and prove the
Postgres-level guarantees regardless of the in-code repository filter:
fail-closed without context, no cross-tenant reads via raw SQL, and no context
leak across transactions sharing a pooled connection.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from shared.context import TenantContext
from shared.db import apply_tenant_context
from shared.repository import Repository
from shared.service import SqlAlchemyUnitOfWork
from tests.models import Gadget

pytestmark = pytest.mark.integration


class GadgetRepository(Repository[Gadget]):
    model = Gadget


async def _seed(factory: async_sessionmaker[AsyncSession], ctx: TenantContext, name: str) -> None:
    async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
        await GadgetRepository(uow.session, ctx).add(Gadget(name=name))


async def test_missing_tenant_ctx_fails_closed(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    """No context set → RLS returns 0 rows, not all rows and not an error."""
    await _seed(session_factory, tenant_ctx, "row")
    async with session_factory() as session:
        # Deliberately do NOT apply any tenant context.
        rows = (await session.execute(text("SELECT id FROM test_gadgets"))).all()
        assert rows == []


async def test_rls_blocks_raw_query_cross_tenant(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
    other_tenant_ctx: TenantContext,
) -> None:
    """Raw SQL bypassing the repository still cannot read another tenant."""
    await _seed(session_factory, tenant_ctx, "a")
    await _seed(session_factory, other_tenant_ctx, "b")
    async with session_factory() as session:
        await apply_tenant_context(session, tenant_ctx)
        rows = (await session.execute(text("SELECT tenant_id FROM test_gadgets"))).scalars().all()
        assert set(rows) == {tenant_ctx.tenant_id}


async def test_tenant_ctx_not_leaked_across_pooled_transactions(
    session_factory: async_sessionmaker[AsyncSession],
    pg_engine: AsyncEngine,
    tenant_ctx: TenantContext,
    other_tenant_ctx: TenantContext,
) -> None:
    """Two sequential transactions on one physical connection do not inherit
    each other's SET LOCAL context (asyncpg pool reuse)."""
    await _seed(session_factory, tenant_ctx, "a")
    await _seed(session_factory, other_tenant_ctx, "b")

    async with pg_engine.connect() as connection:
        # Transaction 1: tenant A context, sees A only.
        async with connection.begin():
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenant_ctx.tenant_id)},
            )
            seen_a = (
                (await connection.execute(text("SELECT tenant_id FROM test_gadgets")))
                .scalars()
                .all()
            )
        assert set(seen_a) == {tenant_ctx.tenant_id}

        # Transaction 2 on the SAME connection: no context re-set → fail closed,
        # not tenant A's rows carried over.
        async with connection.begin():
            leaked = (
                (await connection.execute(text("SELECT tenant_id FROM test_gadgets")))
                .scalars()
                .all()
            )
        assert leaked == []


async def test_write_requires_matching_context(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
    other_tenant_ctx: TenantContext,
) -> None:
    """WITH CHECK rejects inserting a row for a different tenant than the
    session context, even via raw SQL under app_user."""
    with pytest.raises(Exception, match=r"row-level security|policy"):
        async with session_factory() as session:
            await apply_tenant_context(session, tenant_ctx)
            await session.execute(
                text(
                    "INSERT INTO test_gadgets (id, tenant_id, name, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :other, 'x', now(), now())"
                ),
                {"other": str(other_tenant_ctx.tenant_id)},
            )
            await session.commit()
