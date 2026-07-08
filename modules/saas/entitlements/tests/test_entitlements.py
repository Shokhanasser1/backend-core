"""saas.entitlements end-to-end (feature test — moves with the feature).

Uses the ``saas_client`` fixture (root conftest): a running app with
ENABLED_MODULES=saas, so the loader has mounted the router and registered the
RBAC. The tenant grid is a global reference table seeded here as the client
project would (via the owner/migrator role); the tenant's active plan is written
through EntitlementService, exactly as the billing subscriber does.

Covers: the /me overview, the flag/limit resolution + enforcement, the
cancel-at-period-end gate, the mandatory 403 (member cannot read) and 401, and
tenant isolation (RLS) of the active-plan row.

Each direct-service helper builds and disposes its OWN engine so it never reuses
a connection pool across the separate event loops that ``asyncio.run`` spins up
(reusing one would bind a pooled asyncpg connection to a closed loop). The bus
enqueue is silenced so the post-commit publish of saas.entitlement.changed
doesn't reach the web app's arq pool (bound to the TestClient loop).

The app is reached only through the fixture — a feature never imports app.* (that
would cross the modules -> app layer boundary; import-linter enforces it).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from modules.saas.entitlements.service import EntitlementService
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MIGRATOR, ROLE_USER
from shared.errors import ConflictError
from shared.events import bus
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration

# (plan_code, entitlement_key, kind, bool_value, int_value)
_GridRow = tuple[str, str, str, bool | None, int | None]

_PRO_GRID: list[_GridRow] = [
    ("pro", "commerce.api", "flag", True, None),
    ("pro", "commerce.product", "limit", None, 2),
]


def _ctx(tenant_id: UUID) -> TenantContext:
    # actor is arbitrary: tenant-isolation RLS keys on tenant_id, and the
    # entitlement row has no user column.
    return TenantContext(
        tenant_id=tenant_id, actor=Actor(kind="user", id=str(uuid4())), request_id=None
    )


async def _silent_enqueue(handler_id: str, wire: dict[str, Any]) -> None:
    return None


def _silence_bus() -> None:
    """Drop the app's arq pool from the global bus so a post-commit publish from a
    direct-service call in a separate loop is a no-op, not a cross-loop error."""
    bus.bind_enqueue(_silent_enqueue)


async def _seed_grid(migrator_url: str, rows: list[_GridRow]) -> None:
    """Seed the global tariff grid as the owner role (runtime roles are read-only)."""
    engine = create_async_engine(migrator_url)
    try:
        async with engine.begin() as conn:
            for plan, key, kind, bool_v, int_v in rows:
                await conn.execute(
                    text(
                        "INSERT INTO saas_plan_entitlements "
                        "(plan_code, entitlement_key, kind, bool_value, int_value) "
                        "VALUES (:p, :k, :kind, :b, :i)"
                    ),
                    {"p": plan, "k": key, "kind": kind, "b": bool_v, "i": int_v},
                )
    finally:
        await engine.dispose()


async def _set_active_plan(
    user_url: str,
    tenant_id: UUID,
    plan_code: str,
    period_end: datetime | None,
    *,
    then_cancel: bool = False,
) -> None:
    engine = create_async_engine(user_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ctx = _ctx(tenant_id)
        async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
            service = EntitlementService(uow, bus, ctx)
            await service.set_active_plan(plan_code, period_end)
            if then_cancel:
                await service.mark_canceled()
    finally:
        await engine.dispose()


async def _reads(user_url: str, tenant_id: UUID) -> dict[str, object]:
    engine = create_async_engine(user_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ctx = _ctx(tenant_id)
        async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
            service = EntitlementService(uow, bus, ctx)
            return {
                "plan": await service.effective_plan_code(),
                "flag_on": await service.is_enabled("commerce.api"),
                "flag_missing": await service.is_enabled("nope"),
                "limit": await service.get_limit("commerce.product"),
                "limit_missing": await service.get_limit("nope"),
            }
    finally:
        await engine.dispose()


async def _require(user_url: str, tenant_id: UUID, key: str, count: int) -> None:
    engine = create_async_engine(user_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ctx = _ctx(tenant_id)
        async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
            service = EntitlementService(uow, bus, ctx)
            await service.require_within_limit(key, count)
    finally:
        await engine.dispose()


def test_get_me_reflects_active_plan(saas_client: TestClient, role_urls: dict[str, str]) -> None:
    _silence_bus()
    _u, tenant_id, owner = _owner_with_tenant(saas_client, "sha@example.uz")
    asyncio.run(_seed_grid(role_urls[ROLE_MIGRATOR], _PRO_GRID))
    asyncio.run(_set_active_plan(role_urls[ROLE_USER], UUID(tenant_id), "pro", None))

    resp = saas_client.get("/api/saas/entitlements/me", headers=_headers(owner))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_code"] == "pro"
    assert body["flags"] == {"commerce.api": True}
    assert body["limits"] == {"commerce.product": 2}


def test_no_active_plan_is_unconfigured(saas_client: TestClient) -> None:
    # A tenant without a subscription reads as unconfigured: no plan, empty grid.
    _u, _t, owner = _owner_with_tenant(saas_client, "emp@example.uz")
    resp = saas_client.get("/api/saas/entitlements/me", headers=_headers(owner))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"plan_code": None, "flags": {}, "limits": {}}


def test_flags_limits_and_enforcement(saas_client: TestClient, role_urls: dict[str, str]) -> None:
    _silence_bus()
    _u, tenant_raw, _owner = _owner_with_tenant(saas_client, "enf@example.uz")
    tenant_id = UUID(tenant_raw)
    user_url = role_urls[ROLE_USER]
    asyncio.run(_seed_grid(role_urls[ROLE_MIGRATOR], _PRO_GRID))
    asyncio.run(_set_active_plan(user_url, tenant_id, "pro", None))

    reads = asyncio.run(_reads(user_url, tenant_id))
    assert reads == {
        "plan": "pro",
        "flag_on": True,
        "flag_missing": False,
        "limit": 2,
        "limit_missing": None,
    }

    # Enforcement: at 1/2 it passes; at 2/2 the next create is blocked (409).
    asyncio.run(_require(user_url, tenant_id, "commerce.product", 1))
    with pytest.raises(ConflictError):
        asyncio.run(_require(user_url, tenant_id, "commerce.product", 2))
    # An unset limit never blocks.
    asyncio.run(_require(user_url, tenant_id, "nope", 10_000))


def test_cancel_gate(saas_client: TestClient, role_urls: dict[str, str]) -> None:
    _silence_bus()
    user_url = role_urls[ROLE_USER]
    asyncio.run(_seed_grid(role_urls[ROLE_MIGRATOR], _PRO_GRID))
    # Tenant A: canceled and past the period end -> coverage lapsed.
    _ua, a_raw, _oa = _owner_with_tenant(saas_client, "lap@example.uz")
    past = datetime.now(UTC) - timedelta(days=1)
    asyncio.run(_set_active_plan(user_url, UUID(a_raw), "pro", past, then_cancel=True))
    lapsed = asyncio.run(_reads(user_url, UUID(a_raw)))
    assert lapsed["plan"] is None
    assert lapsed["flag_on"] is False

    # Tenant B: canceled but still inside the paid period -> still covered.
    _ub, b_raw, _ob = _owner_with_tenant(saas_client, "grace@example.uz")
    future = datetime.now(UTC) + timedelta(days=1)
    asyncio.run(_set_active_plan(user_url, UUID(b_raw), "pro", future, then_cancel=True))
    covered = asyncio.run(_reads(user_url, UUID(b_raw)))
    assert covered["plan"] == "pro"
    assert covered["flag_on"] is True


def test_tenant_isolation(saas_client: TestClient, role_urls: dict[str, str]) -> None:
    _silence_bus()
    user_url = role_urls[ROLE_USER]
    asyncio.run(_seed_grid(role_urls[ROLE_MIGRATOR], _PRO_GRID))
    _ua, a_raw, _oa = _owner_with_tenant(saas_client, "alfa@example.uz")
    _ub, b_raw, _ob = _owner_with_tenant(saas_client, "beta@example.uz")
    asyncio.run(_set_active_plan(user_url, UUID(a_raw), "pro", None))

    # A has the plan; B (no row of its own) cannot see A's active-plan row (RLS).
    assert asyncio.run(_reads(user_url, UUID(a_raw)))["plan"] == "pro"
    b_reads = asyncio.run(_reads(user_url, UUID(b_raw)))
    assert b_reads["plan"] is None
    assert b_reads["flag_on"] is False


def test_me_requires_permission(saas_client: TestClient) -> None:
    _u, tenant_id, owner = _owner_with_tenant(saas_client, "boss@example.uz")
    member = _member_of(saas_client, owner, tenant_id, "clerk@example.uz")
    # Member lacks saas.entitlement:read -> 403 (DoD negative test).
    assert saas_client.get("/api/saas/entitlements/me", headers=_headers(member)).status_code == 403
    # No token -> 401.
    assert saas_client.get("/api/saas/entitlements/me").status_code == 401
