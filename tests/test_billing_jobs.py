"""Billing background paths: checkout-expiry sweep + auto-subscribe subscriber.

Integration tests against a real Postgres. The expiry sweep runs as a cross-tenant
scan (app_maintenance) that expires each abandoned checkout in its own tenant
context; the subscriber reacts to tenants.tenant.created by creating the free
subscription in the new tenant's context (app_user).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from core.billing.jobs import expire_stale_checkouts
from core.billing.subscribers import auto_subscribe_new_tenant
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MIGRATOR
from shared.events import EventBus, EventEnvelope
from shared.handler_runtime import HandlerRuntime, reset_handler_runtime, set_handler_runtime
from shared.ids import new_uuid7
from shared.service import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

PRICE = 50_000


async def _seed_reference_and_tenant(role_urls: dict[str, str]) -> tuple[object, object]:
    tenant_id, user_id = uuid4(), uuid4()
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO currencies (code, exponent, name) VALUES ('UZS', 0, 'Som')")
            )
            await conn.execute(
                text(
                    "INSERT INTO plans (id, code, name, price_amount, trial_days) "
                    "VALUES (:id, 'free', CAST(:name AS jsonb), 0, 0)"
                ),
                {"id": uuid4(), "name": '{"ru": "Free", "uz": "Bepul"}'},
            )
            await conn.execute(
                text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
                {"id": user_id, "email": f"{user_id}@example.uz"},
            )
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, owner_user_id) "
                    "VALUES (:id, 'Test', :slug, :owner)"
                ),
                {"id": tenant_id, "slug": str(tenant_id), "owner": user_id},
            )
    finally:
        await engine.dispose()
    return tenant_id, user_id


async def _seed_payment(
    role_urls: dict[str, str], tenant_id: object, *, status: str, age_hours: int
) -> object:
    payment_id = uuid4()
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO payments (id, tenant_id, purpose, reference, amount, currency, "
                    "status, provider, idempotency_key, created_at) VALUES "
                    "(:id, :t, 'topup', :ref, :amt, 'UZS', :st, 'payme', :ik, "
                    "now() - make_interval(hours => :age))"
                ),
                {
                    "id": payment_id,
                    "t": tenant_id,
                    "ref": str(payment_id),
                    "amt": PRICE,
                    "st": status,
                    "ik": str(uuid4()),
                    "age": age_hours,
                },
            )
    finally:
        await engine.dispose()
    return payment_id


async def _payment_status(role_urls: dict[str, str], payment_id: object) -> str:
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT status FROM payments WHERE id = :id"), {"id": payment_id}
                )
            ).scalar_one()
            return str(row)
    finally:
        await engine.dispose()


# --------------------------------------------------------------- expiry sweep


async def test_expiry_sweep_expires_only_stale_live_payments(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    tenant_id, _ = await _seed_reference_and_tenant(role_urls)
    # TTL is 3600s by default; 2h-old created/pending are stale, a fresh one is not.
    stale_created = await _seed_payment(role_urls, tenant_id, status="created", age_hours=2)
    stale_pending = await _seed_payment(role_urls, tenant_id, status="pending", age_hours=2)
    fresh = await _seed_payment(role_urls, tenant_id, status="created", age_hours=0)
    already = await _seed_payment(role_urls, tenant_id, status="succeeded", age_hours=5)

    count = await expire_stale_checkouts(maintenance_session_factory, EventBus(), test_settings)

    assert count == 2
    assert await _payment_status(role_urls, stale_created) == "expired"
    assert await _payment_status(role_urls, stale_pending) == "expired"
    assert await _payment_status(role_urls, fresh) == "created"  # within TTL
    assert await _payment_status(role_urls, already) == "succeeded"  # terminal, untouched


async def test_expiry_sweep_no_op_when_nothing_stale(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    tenant_id, _ = await _seed_reference_and_tenant(role_urls)
    await _seed_payment(role_urls, tenant_id, status="created", age_hours=0)

    assert await expire_stale_checkouts(maintenance_session_factory, EventBus(), test_settings) == 0


# ----------------------------------------------------------- auto-subscribe


def _settings(**overrides: object) -> Settings:
    params: dict[str, object] = {
        "billing_auto_subscribe": True,
        "billing_default_plan_code": "free",
    }
    params.update(overrides)
    return Settings(_env_file=None, **params)  # type: ignore[arg-type]


def _tenant_created_event(tenant_id: object, user_id: object) -> EventEnvelope:
    # Mirrors TenantService.create_tenant: user scope, so the envelope tenant_id
    # is None and the new tenant id lives in the payload.
    return EventEnvelope(
        event_id=new_uuid7(),
        name="tenants.tenant.created",
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=None,
        actor=Actor(kind="user", id=str(user_id)),
        payload={"tenant_id": str(tenant_id), "name": "Test", "owner_user_id": str(user_id)},
    )


async def _run_subscriber(
    session_factory: async_sessionmaker[AsyncSession], event: EventEnvelope
) -> None:
    """Drive the reliable subscriber exactly as the arq dispatcher would: inside a
    UoW carrying the envelope context, with the handler runtime published."""
    ctx = TenantContext(tenant_id=event.tenant_id, actor=event.actor, request_id=None)
    async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
        token = set_handler_runtime(HandlerRuntime(uow=uow, ctx=ctx, bus=EventBus()))
        try:
            await auto_subscribe_new_tenant(event)
        finally:
            reset_handler_runtime(token)


async def _subscription_count(role_urls: dict[str, str], tenant_id: object) -> int:
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text("SELECT count(*) FROM subscriptions WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
            ).scalar_one()
            return int(count)
    finally:
        await engine.dispose()


async def test_auto_subscribe_creates_free_subscription(
    session_factory: async_sessionmaker[AsyncSession],
    role_urls: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.billing.subscribers.get_settings", _settings)
    tenant_id, user_id = await _seed_reference_and_tenant(role_urls)

    await _run_subscriber(session_factory, _tenant_created_event(tenant_id, user_id))

    assert await _subscription_count(role_urls, tenant_id) == 1
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.connect() as conn:
            status = (
                await conn.execute(
                    text("SELECT status FROM subscriptions WHERE tenant_id = :t"), {"t": tenant_id}
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert status == "active"  # free plan, trial_days 0 -> immediately active


async def test_auto_subscribe_disabled_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    role_urls: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.billing.subscribers.get_settings", lambda: _settings(billing_auto_subscribe=False)
    )
    tenant_id, user_id = await _seed_reference_and_tenant(role_urls)

    await _run_subscriber(session_factory, _tenant_created_event(tenant_id, user_id))

    assert await _subscription_count(role_urls, tenant_id) == 0
