"""saas.metering end-to-end (feature test — moves with the feature).

Uses the ``saas_client`` fixture (ENABLED_MODULES=saas) so the loader mounted the
router + RBAC. Usage is written through MeteringService.record (its public API);
the /me overview, window queries, the mandatory 403/401 and tenant isolation are
exercised.

Each direct-service helper builds and disposes its OWN engine so it never reuses a
connection pool across the separate event loops ``asyncio.run`` spins up. record()
emits no events, so no bus silencing is needed.

The app is reached only through the fixture — a feature never imports app.* (that
would cross the modules -> app layer boundary; import-linter enforces it).
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from modules.saas.metering.service import MeteringService
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_USER
from shared.events import bus
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


def _ctx(tenant_id: UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, actor=Actor(kind="user", id=str(uuid4())), request_id=None
    )


async def _record(
    user_url: str, tenant_id: UUID, metric: str, delta: int = 1, at: datetime | None = None
) -> None:
    engine = create_async_engine(user_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ctx = _ctx(tenant_id)
        async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
            await MeteringService(uow, bus, ctx).record(metric, delta, at=at)
    finally:
        await engine.dispose()


async def _usage(
    user_url: str,
    tenant_id: UUID,
    metric: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> int:
    engine = create_async_engine(user_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ctx = _ctx(tenant_id)
        async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
            return await MeteringService(uow, bus, ctx).usage(metric, since=since, until=until)
    finally:
        await engine.dispose()


async def _summary(user_url: str, tenant_id: UUID) -> dict[str, int]:
    engine = create_async_engine(user_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ctx = _ctx(tenant_id)
        async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
            return await MeteringService(uow, bus, ctx).summary()
    finally:
        await engine.dispose()


def test_record_accumulates(saas_client: TestClient, role_urls: dict[str, str]) -> None:
    _u, tenant_raw, _owner = _owner_with_tenant(saas_client, "acc@example.uz")
    tenant_id = UUID(tenant_raw)
    user_url = role_urls[ROLE_USER]
    asyncio.run(_record(user_url, tenant_id, "commerce.order"))
    asyncio.run(_record(user_url, tenant_id, "commerce.order", 3))
    asyncio.run(_record(user_url, tenant_id, "api.call"))

    assert asyncio.run(_usage(user_url, tenant_id, "commerce.order")) == 4
    assert asyncio.run(_usage(user_url, tenant_id, "api.call")) == 1
    assert asyncio.run(_usage(user_url, tenant_id, "missing")) == 0
    assert asyncio.run(_summary(user_url, tenant_id)) == {"commerce.order": 4, "api.call": 1}


def test_usage_day_window(saas_client: TestClient, role_urls: dict[str, str]) -> None:
    _u, tenant_raw, _owner = _owner_with_tenant(saas_client, "win@example.uz")
    tenant_id = UUID(tenant_raw)
    user_url = role_urls[ROLE_USER]
    today = datetime.now(UTC)
    old = today - timedelta(days=10)
    asyncio.run(_record(user_url, tenant_id, "commerce.order", 5, at=old))
    asyncio.run(_record(user_url, tenant_id, "commerce.order", 2, at=today))

    # Full history sums both buckets; a since-window keeps only today's.
    assert asyncio.run(_usage(user_url, tenant_id, "commerce.order")) == 7
    assert asyncio.run(_usage(user_url, tenant_id, "commerce.order", since=today.date())) == 2
    assert asyncio.run(_usage(user_url, tenant_id, "commerce.order", until=old.date())) == 5


def test_get_me_overview(saas_client: TestClient, role_urls: dict[str, str]) -> None:
    _u, tenant_raw, owner = _owner_with_tenant(saas_client, "ovw@example.uz")
    tenant_id = UUID(tenant_raw)
    asyncio.run(_record(role_urls[ROLE_USER], tenant_id, "commerce.order", 2))

    resp = saas_client.get("/api/saas/usage/me", headers=_headers(owner))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metrics"] == {"commerce.order": 2}
    assert body["since"] is None and body["until"] is None


def test_tenant_isolation(saas_client: TestClient, role_urls: dict[str, str]) -> None:
    _ua, a_raw, _oa = _owner_with_tenant(saas_client, "alfa@example.uz")
    _ub, b_raw, _ob = _owner_with_tenant(saas_client, "beta@example.uz")
    user_url = role_urls[ROLE_USER]
    asyncio.run(_record(user_url, UUID(a_raw), "commerce.order", 9))

    # A sees its usage; B (its own tenant) sees nothing (RLS scopes the counters).
    assert asyncio.run(_usage(user_url, UUID(a_raw), "commerce.order")) == 9
    assert asyncio.run(_usage(user_url, UUID(b_raw), "commerce.order")) == 0
    assert asyncio.run(_summary(user_url, UUID(b_raw))) == {}


def test_me_requires_permission(saas_client: TestClient) -> None:
    _u, tenant_id, owner = _owner_with_tenant(saas_client, "boss@example.uz")
    member = _member_of(saas_client, owner, tenant_id, "clerk@example.uz")
    # Member lacks saas.usage:read -> 403 (DoD negative test).
    assert saas_client.get("/api/saas/usage/me", headers=_headers(member)).status_code == 403
    # No token -> 401.
    assert saas_client.get("/api/saas/usage/me").status_code == 401
