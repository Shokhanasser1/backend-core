"""saas.entitlements hardening (feature test — moves with the feature): a paid plan
whose period ended (billing.subscription.ended) stops granting anything without
clobbering a newer plan, a canceled plan with no known period end is not covered
forever, and a limit holds under concurrency (checked under a per-tenant lock)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from modules.saas.entitlements.service import EntitlementService
from modules.saas.entitlements.subscribers import on_subscription_ended
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MIGRATOR, ROLE_USER
from shared.errors import ConflictError
from shared.events import EventBus, EventEnvelope, bus
from shared.handler_runtime import HandlerRuntime, reset_handler_runtime, set_handler_runtime
from shared.ids import new_uuid7
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _owner_with_tenant

pytestmark = pytest.mark.integration


async def _silent_enqueue(handler_id: str, wire: dict[str, Any]) -> None:
    return None


def _ctx(tenant_id: UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, actor=Actor(kind="system", id="test"), request_id=None
    )


async def _with_service(user_url: str, tenant_id: UUID, action: Any) -> Any:
    engine = create_async_engine(user_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        ctx = _ctx(tenant_id)
        async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
            return await action(EntitlementService(uow, bus, ctx), uow)
    finally:
        await engine.dispose()


async def _ended(user_url: str, tenant_id: UUID, plan_code: str) -> None:
    engine = create_async_engine(user_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        ctx = _ctx(tenant_id)
        envelope = EventEnvelope(
            event_id=new_uuid7(),
            name="billing.subscription.ended",
            version=1,
            occurred_at=datetime.now(UTC),
            tenant_id=tenant_id,
            actor=ctx.actor,
            payload={"subscription_id": str(uuid4()), "plan_code": plan_code, "status": "expired"},
        )
        async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
            token = set_handler_runtime(HandlerRuntime(uow=uow, ctx=ctx, bus=EventBus()))
            try:
                await on_subscription_ended(envelope)
            finally:
                reset_handler_runtime(token)
    finally:
        await engine.dispose()


def _plan(user_url: str, tenant_id: UUID) -> str | None:
    async def read(service: EntitlementService, _uow: object) -> str | None:
        return await service.effective_plan_code()

    return asyncio.run(_with_service(user_url, tenant_id, read))  # type: ignore[no-any-return]


def _activate(user_url: str, tenant_id: UUID, plan: str, end: datetime | None) -> None:
    async def act(service: EntitlementService, _uow: object) -> None:
        await service.set_active_plan(plan, end)

    asyncio.run(_with_service(user_url, tenant_id, act))


def test_ended_paid_plan_lapses_but_a_newer_plan_survives(
    saas_client: TestClient, role_urls: dict[str, str]
) -> None:
    bus.bind_enqueue(_silent_enqueue)
    user_url = role_urls[ROLE_USER]
    _u, raw, _o = _owner_with_tenant(saas_client, "end@example.uz")
    tenant_id = UUID(raw)
    _activate(user_url, tenant_id, "pro", datetime.now(UTC) + timedelta(days=3))

    asyncio.run(_ended(user_url, tenant_id, "pro"))
    assert _plan(user_url, tenant_id) is None

    # The free fallback activated first (events may arrive in any order): a late
    # "ended" for the OLD plan must not clear the new one.
    _activate(user_url, tenant_id, "free", datetime.now(UTC) + timedelta(days=30))
    asyncio.run(_ended(user_url, tenant_id, "pro"))
    assert _plan(user_url, tenant_id) == "free"


def test_canceled_plan_without_period_end_is_not_covered_forever(
    saas_client: TestClient, role_urls: dict[str, str]
) -> None:
    bus.bind_enqueue(_silent_enqueue)
    user_url = role_urls[ROLE_USER]
    _u, raw, _o = _owner_with_tenant(saas_client, "nul@example.uz")

    async def cancel_unknown_end(service: EntitlementService, _uow: object) -> None:
        await service.set_active_plan("pro", None)
        await service.mark_canceled()

    asyncio.run(_with_service(user_url, UUID(raw), cancel_unknown_end))

    assert _plan(user_url, UUID(raw)) is None


def test_limit_holds_under_concurrency(saas_client: TestClient, role_urls: dict[str, str]) -> None:
    bus.bind_enqueue(_silent_enqueue)
    user_url = role_urls[ROLE_USER]
    _u, raw, _o = _owner_with_tenant(saas_client, "lim@example.uz")
    tenant_id = UUID(raw)

    async def seed() -> None:
        engine = create_async_engine(role_urls[ROLE_MIGRATOR])
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO saas_plan_entitlements "
                        "(plan_code, entitlement_key, kind, int_value) "
                        "VALUES ('pro', 'test.gadget', 'limit', 1)"
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(seed())
    _activate(user_url, tenant_id, "pro", None)

    async def create_gadget(service: EntitlementService, uow: Any) -> None:
        async def count() -> int:
            return int(
                (await uow.session.execute(text("SELECT count(*) FROM test_gadgets"))).scalar_one()
            )

        await service.require_within_limit("test.gadget", count)
        await asyncio.sleep(0.2)  # widen the window between the check and the insert
        await uow.session.execute(
            text("INSERT INTO test_gadgets (id, tenant_id, name) VALUES (:id, :t, 'g')"),
            {"id": uuid4(), "t": tenant_id},
        )

    async def race() -> list[object]:
        async def attempt() -> object:
            try:
                return await _with_service(user_url, tenant_id, create_gadget)
            except ConflictError as exc:
                return exc

        return list(await asyncio.gather(attempt(), attempt()))

    results = asyncio.run(race())

    assert sum(isinstance(r, ConflictError) for r in results) == 1

    async def gadgets() -> int:
        engine = create_async_engine(role_urls[ROLE_MIGRATOR])
        try:
            async with engine.connect() as conn:
                return int(
                    (await conn.execute(text("SELECT count(*) FROM test_gadgets"))).scalar_one()
                )
        finally:
            await engine.dispose()

    assert asyncio.run(gadgets()) == 1
