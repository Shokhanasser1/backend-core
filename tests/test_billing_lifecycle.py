"""Subscription/payment lifecycle hardening (audit batch 2).

Integration tests against a real Postgres:
- upgrade from the free plan: a paid PENDING subscription lives alongside the free
  one; payment success closes the free one and activates the paid one; an
  abandoned checkout drops the pending one and the free plan stays;
- a provider success that arrives after our checkout expiry is still honored
  (money moved — it is never dropped);
- create_payment idempotency is race-safe and refuses a reused key with
  different parameters;
- payment status races: the expiry sweep and the webhook lock the payment row,
  so a concurrent transition is never overwritten;
- the period-end sweep closes ended paid subscriptions and falls back to free.
"""

import asyncio
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from core.billing.adapters.payme import PaymeProvider
from core.billing.jobs import expire_ended_subscriptions, expire_stale_checkouts
from core.billing.models import Payment
from core.billing.ports import (
    CallbackOutcome,
    PaymentProvider,
    ProviderCallback,
    RawWebhook,
    WebhookResponse,
)
from core.billing.schemas import CheckoutDTO, PaymentDTO
from core.billing.service import BillingService, PaymentService
from core.billing.webhooks import WebhookProcessor
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MIGRATOR
from shared.errors import ConflictError, InvariantViolationError
from shared.events import EventBus
from shared.money import Money
from shared.service import SqlAlchemyUnitOfWork
from tests.test_billing_webhooks import PAYME_ERR_UNABLE_TO_PERFORM, _err_code, _payme_raw

pytestmark = pytest.mark.integration

PRO_PRICE = 50_000


class FakeProvider:
    code: ClassVar[str] = "fake"

    def __init__(self, code: str = "fake") -> None:
        self.code = code  # type: ignore[misc]
        self.checkouts: list[UUID] = []

    async def create_checkout(self, payment: PaymentDTO, return_url: str | None) -> CheckoutDTO:
        self.checkouts.append(payment.id)
        await asyncio.sleep(0.05)  # widen the race window for the concurrency test
        return CheckoutDTO(
            payment_id=payment.id,
            provider=self.code,
            checkout_url=f"https://pay.example/{payment.id}",
            expires_at=None,
        )

    def parse_webhook(self, raw: RawWebhook) -> ProviderCallback:  # pragma: no cover
        raise NotImplementedError

    def build_webhook_response(
        self, outcome: CallbackOutcome
    ) -> WebhookResponse:  # pragma: no cover
        raise NotImplementedError


async def _exec(
    role_urls: dict[str, str], sql: str, params: dict[str, object] | None = None
) -> None:
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


async def _rows(
    role_urls: dict[str, str], sql: str, params: dict[str, object] | None = None
) -> list[dict[str, object]]:
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.connect() as conn:
            return [dict(r) for r in (await conn.execute(text(sql), params or {})).mappings()]
    finally:
        await engine.dispose()


@pytest.fixture
async def ctx(role_urls: dict[str, str], _clean_db: None) -> TenantContext:
    tenant_id, user_id = uuid4(), uuid4()
    await _exec(role_urls, "INSERT INTO currencies (code, exponent, name) VALUES ('UZS', 0, 'Som')")
    for code, price in (("free", 0), ("pro", PRO_PRICE), ("max", PRO_PRICE * 2)):
        await _exec(
            role_urls,
            "INSERT INTO plans (id, code, name, price_amount) "
            "VALUES (:id, :code, CAST(:name AS jsonb), :price)",
            {"id": uuid4(), "code": code, "name": '{"ru": "P", "uz": "P"}', "price": price},
        )
    await _exec(
        role_urls,
        "INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')",
        {"id": user_id, "email": f"{user_id}@example.uz"},
    )
    await _exec(
        role_urls,
        "INSERT INTO tenants (id, name, slug, owner_user_id) VALUES (:id, 'T', :slug, :owner)",
        {"id": tenant_id, "slug": str(tenant_id), "owner": user_id},
    )
    return TenantContext(
        tenant_id=tenant_id, actor=Actor(kind="user", id=str(user_id)), request_id="req"
    )


def _services(
    uow: SqlAlchemyUnitOfWork, ctx: TenantContext, settings: Settings, *providers: PaymentProvider
) -> tuple[BillingService, PaymentService]:
    bus = EventBus()
    payments = PaymentService(
        uow, bus, ctx, providers={p.code: p for p in providers}, settings=settings
    )
    return BillingService(uow, bus, ctx, payments=payments), payments


async def _subs(role_urls: dict[str, str], tenant_id: object) -> dict[str, str]:
    """plan code -> status of every subscription of the tenant."""
    rows = await _rows(
        role_urls,
        "SELECT p.code, s.status FROM subscriptions s JOIN plans p ON p.id = s.plan_id "
        "WHERE s.tenant_id = :t ORDER BY s.created_at",
        {"t": tenant_id},
    )
    return {str(r["code"]): str(r["status"]) for r in rows}


async def _free_then_pro(
    factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    settings: Settings,
    provider: FakeProvider,
) -> CheckoutDTO:
    async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
        billing, _ = _services(uow, ctx, settings, provider)
        await billing.auto_subscribe("free")
    async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
        billing, _ = _services(uow, ctx, settings, provider)
        return await billing.start_subscription("pro", provider.code)


async def _succeed(
    factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    settings: Settings,
    payment_id: UUID,
) -> None:
    async with SqlAlchemyUnitOfWork(factory, context=ctx) as uow:
        _, payments = _services(uow, ctx, settings)
        payment = await uow.session.get(Payment, payment_id)
        assert payment is not None
        await payments.mark_succeeded(payment)


# ------------------------------------------------------------------ upgrade


async def test_upgrade_from_free_keeps_free_live_until_paid(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    checkout = await _free_then_pro(session_factory, ctx, test_settings, FakeProvider())

    assert checkout.checkout_url
    assert await _subs(role_urls, ctx.tenant_id) == {"free": "active", "pro": "pending"}


async def test_payment_success_replaces_free_with_paid(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    checkout = await _free_then_pro(session_factory, ctx, test_settings, FakeProvider())

    await _succeed(session_factory, ctx, test_settings, checkout.payment_id)

    assert await _subs(role_urls, ctx.tenant_id) == {"free": "canceled", "pro": "active"}


async def test_restart_same_checkout_is_idempotent_other_conflicts(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    fake, other = FakeProvider(), FakeProvider("other")
    first = await _free_then_pro(session_factory, ctx, test_settings, fake)

    async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
        billing, _ = _services(uow, ctx, test_settings, fake, other)
        again = await billing.start_subscription("pro", fake.code)
    assert again.payment_id == first.payment_id
    assert fake.checkouts == [first.payment_id]  # no second provider checkout

    for plan, provider in (("pro", other.code), ("max", fake.code)):
        with pytest.raises(ConflictError):
            async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
                billing, _ = _services(uow, ctx, test_settings, fake, other)
                await billing.start_subscription(plan, provider)
    assert await _subs(role_urls, ctx.tenant_id) == {"free": "active", "pro": "pending"}


async def test_start_subscription_rejects_free_plan(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
) -> None:
    fake = FakeProvider()
    with pytest.raises(InvariantViolationError):
        async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
            billing, _ = _services(uow, ctx, test_settings, fake)
            await billing.start_subscription("free", fake.code)


async def test_renewal_of_same_plan_extends_from_current_period_end(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    fake = FakeProvider()
    first = await _free_then_pro(session_factory, ctx, test_settings, fake)
    await _succeed(session_factory, ctx, test_settings, first.payment_id)
    old_end = (
        await _rows(
            role_urls,
            "SELECT current_period_end AS e FROM subscriptions WHERE status = 'active'",
        )
    )[0]["e"]

    async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
        billing, _ = _services(uow, ctx, test_settings, fake)
        second = await billing.start_subscription("pro", fake.code)
    await _succeed(session_factory, ctx, test_settings, second.payment_id)

    live = await _rows(
        role_urls,
        "SELECT current_period_start AS s FROM subscriptions WHERE status = 'active'",
    )
    assert len(live) == 1
    assert live[0]["s"] == old_end  # paid early: the new period starts where the old ends


# ------------------------------------------------------- abandoned / late paid


async def test_checkout_expiry_drops_pending_and_free_stays(
    session_factory: async_sessionmaker[AsyncSession],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    checkout = await _free_then_pro(session_factory, ctx, test_settings, FakeProvider())
    await _exec(
        role_urls,
        "UPDATE payments SET created_at = now() - interval '2 hours' WHERE id = :id",
        {"id": checkout.payment_id},
    )

    assert await expire_stale_checkouts(maintenance_session_factory, EventBus(), test_settings) == 1

    assert await _subs(role_urls, ctx.tenant_id) == {"free": "active", "pro": "expired"}


async def test_late_provider_success_after_expiry_is_honored(
    session_factory: async_sessionmaker[AsyncSession],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    checkout = await _free_then_pro(session_factory, ctx, test_settings, FakeProvider())
    await _exec(
        role_urls,
        "UPDATE payments SET created_at = now() - interval '2 hours' WHERE id = :id",
        {"id": checkout.payment_id},
    )
    await expire_stale_checkouts(maintenance_session_factory, EventBus(), test_settings)

    # The provider confirms after our TTL: money moved, so it must not be dropped.
    await _succeed(session_factory, ctx, test_settings, checkout.payment_id)

    status = await _rows(
        role_urls, "SELECT status FROM payments WHERE id = :id", {"id": checkout.payment_id}
    )
    assert status[0]["status"] == "succeeded"
    assert await _subs(role_urls, ctx.tenant_id) == {"free": "canceled", "pro": "active"}


# ------------------------------------------------------------ idempotency


async def test_reused_idempotency_key_with_other_params_conflicts(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
) -> None:
    fake = FakeProvider()
    async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
        _, payments = _services(uow, ctx, test_settings, fake)
        await payments.create_payment(
            Money(1000, "UZS"),
            purpose="topup",
            reference="r1",
            provider=fake.code,
            idempotency_key="k1",
        )
    with pytest.raises(ConflictError):
        async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
            _, payments = _services(uow, ctx, test_settings, fake)
            await payments.create_payment(
                Money(9999, "UZS"),
                purpose="topup",
                reference="r1",
                provider=fake.code,
                idempotency_key="k1",
            )


async def test_concurrent_create_payment_same_key_single_row(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
) -> None:
    fake = FakeProvider()

    async def create() -> CheckoutDTO:
        async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
            _, payments = _services(uow, ctx, test_settings, fake)
            return await payments.create_payment(
                Money(1000, "UZS"),
                purpose="topup",
                reference="r1",
                provider=fake.code,
                idempotency_key="race",
            )

    first, second = await asyncio.gather(create(), create())

    assert first.payment_id == second.payment_id
    assert first.checkout_url == second.checkout_url
    async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
        count = (await uow.session.execute(select(func.count()).select_from(Payment))).scalar_one()
    assert count == 1


@pytest.mark.parametrize(
    "return_url",
    ["javascript:alert(1)", "https://shop.example/ok;a=1", "//evil.example", "https://a b.example"],
)
async def test_create_payment_rejects_unsafe_return_url(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    return_url: str,
) -> None:
    fake = FakeProvider()
    with pytest.raises(InvariantViolationError):
        async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
            _, payments = _services(uow, ctx, test_settings, fake)
            await payments.create_payment(
                Money(1000, "UZS"),
                purpose="topup",
                reference="r1",
                provider=fake.code,
                idempotency_key="u",
                return_url=return_url,
            )
    assert fake.checkouts == []


# ----------------------------------------------------------- status races


async def _hold_and_set(
    role_urls: dict[str, str], payment_id: object, status: str, locked: asyncio.Event
) -> None:
    """Lock the payment row, change its status, and commit only after a delay —
    a concurrent transition racing in between."""
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT id FROM payments WHERE id = :id FOR UPDATE"), {"id": payment_id}
            )
            await conn.execute(
                text("UPDATE payments SET status = :st WHERE id = :id"),
                {"st": status, "id": payment_id},
            )
            locked.set()
            await asyncio.sleep(0.5)
    finally:
        await engine.dispose()


async def test_expiry_sweep_does_not_overwrite_concurrent_success(
    session_factory: async_sessionmaker[AsyncSession],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    checkout = await _free_then_pro(session_factory, ctx, test_settings, FakeProvider())
    await _exec(
        role_urls,
        "UPDATE payments SET created_at = now() - interval '2 hours' WHERE id = :id",
        {"id": checkout.payment_id},
    )
    locked = asyncio.Event()
    holder = asyncio.create_task(_hold_and_set(role_urls, checkout.payment_id, "succeeded", locked))
    await locked.wait()

    expired = await expire_stale_checkouts(maintenance_session_factory, EventBus(), test_settings)
    await holder

    assert expired == 0
    rows = await _rows(
        role_urls, "SELECT status FROM payments WHERE id = :id", {"id": checkout.payment_id}
    )
    assert rows[0]["status"] == "succeeded"


async def test_webhook_does_not_overwrite_concurrent_cancel(
    session_factory: async_sessionmaker[AsyncSession],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    payme = PaymeProvider(merchant_id="m", merchant_key="test-merchant-key")
    async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
        billing, _ = _services(uow, ctx, test_settings, payme)
        checkout = await billing.start_subscription("pro", "payme")
    await _exec(
        role_urls,
        "UPDATE payments SET status = 'pending', provider_transaction_id = 'tx-race' "
        "WHERE id = :id",
        {"id": checkout.payment_id},
    )
    locked = asyncio.Event()
    holder = asyncio.create_task(_hold_and_set(role_urls, checkout.payment_id, "canceled", locked))
    await locked.wait()

    processor = WebhookProcessor(
        maintenance_sessions=maintenance_session_factory,
        bus=EventBus(),
        providers={"payme": payme},
        settings=test_settings,
    )
    _, body = await processor.process("payme", _payme_raw("PerformTransaction", {"id": "tx-race"}))
    await holder

    assert _err_code(body) == PAYME_ERR_UNABLE_TO_PERFORM
    rows = await _rows(
        role_urls, "SELECT status FROM payments WHERE id = :id", {"id": checkout.payment_id}
    )
    assert rows[0]["status"] == "canceled"
    assert (await _subs(role_urls, ctx.tenant_id))["pro"] != "active"


# ------------------------------------------------------------- period end


async def _seed_sub(
    role_urls: dict[str, str],
    tenant_id: object,
    plan: str,
    *,
    ended_days_ago: int,
    status: str = "active",
    cancel_at_period_end: bool = False,
) -> UUID:
    sub_id = uuid4()
    await _exec(
        role_urls,
        "INSERT INTO subscriptions (id, tenant_id, plan_id, status, price_amount, currency, "
        "current_period_start, current_period_end, cancel_at_period_end) "
        "SELECT :id, :t, id, :st, price_amount, 'UZS', now() - interval '40 days', "
        "now() - make_interval(days => :ago), :cancel FROM plans WHERE code = :plan",
        {
            "id": sub_id,
            "t": tenant_id,
            "st": status,
            "ago": ended_days_ago,
            "cancel": cancel_at_period_end,
            "plan": plan,
        },
    )
    return sub_id


@pytest.mark.parametrize(("cancel", "closed"), [(False, "expired"), (True, "canceled")])
async def test_ended_paid_subscription_closes_and_falls_back_to_free(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
    cancel: bool,
    closed: str,
) -> None:
    await _seed_sub(role_urls, ctx.tenant_id, "pro", ended_days_ago=1, cancel_at_period_end=cancel)

    count = await expire_ended_subscriptions(maintenance_session_factory, EventBus(), test_settings)

    assert count == 1
    assert await _subs(role_urls, ctx.tenant_id) == {"pro": closed, "free": "active"}


async def test_ended_free_subscription_rolls_forward(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    sub_id = await _seed_sub(role_urls, ctx.tenant_id, "free", ended_days_ago=45)

    await expire_ended_subscriptions(maintenance_session_factory, EventBus(), test_settings)

    rows = await _rows(
        role_urls,
        "SELECT status, current_period_end AS e FROM subscriptions WHERE id = :id",
        {"id": sub_id},
    )
    assert rows[0]["status"] == "active"
    assert rows[0]["e"] > datetime.now(UTC)  # type: ignore[operator]
    assert await _subs(role_urls, ctx.tenant_id) == {"free": "active"}


async def test_period_end_sweep_ignores_live_periods(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    await _seed_sub(role_urls, ctx.tenant_id, "pro", ended_days_ago=-5)  # ends in 5 days

    assert (
        await expire_ended_subscriptions(maintenance_session_factory, EventBus(), test_settings)
        == 0
    )
    assert await _subs(role_urls, ctx.tenant_id) == {"pro": "active"}
