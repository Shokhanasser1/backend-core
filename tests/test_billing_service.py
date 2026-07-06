"""BillingService + PaymentService integration tests (interfaces §3.3).

Task 11 of Phase 3: exercise the service layer against a real Postgres with a
FAKE PaymentProvider (the real Payme/Click adapters land in task 12). Verifies:
- start_subscription -> one pending subscription + one created payment;
- mark_succeeded of a subscription payment -> subscription active in the SAME
  transaction ("money taken, subscription inactive" impossible by construction);
- create_payment idempotency by (tenant, idempotency_key);
- the one-live-subscription-per-tenant invariant (partial-unique -> ConflictError);
- auto_subscribe of the zero-price plan (no payment, immediately active).
"""

from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from core.billing.models import Payment, Subscription
from core.billing.ports import CallbackOutcome, ProviderCallback, RawWebhook, WebhookResponse
from core.billing.schemas import CheckoutDTO, PaymentDTO
from core.billing.service import BillingService, PaymentService
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MIGRATOR
from shared.errors import ConflictError
from shared.events import EventBus
from shared.money import Money
from shared.service import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

FREE_PLAN = "free"
PRO_PLAN = "pro"
PRO_PRICE = 50_000  # minor units, UZS (exponent 0)


class FakePaymentProvider:
    """A minimal PaymentProvider double: records outgoing checkouts. The webhook
    methods are exercised by task 12/13 tests, not here."""

    code: ClassVar[str] = "fake"

    def __init__(self) -> None:
        self.checkouts: list[UUID] = []

    async def create_checkout(self, payment: PaymentDTO, return_url: str | None) -> CheckoutDTO:
        self.checkouts.append(payment.id)
        return CheckoutDTO(
            payment_id=payment.id,
            provider=self.code,
            checkout_url=f"https://pay.example/checkout/{payment.id}",
            expires_at=None,
        )

    def parse_webhook(self, raw: RawWebhook) -> ProviderCallback:  # pragma: no cover - task 13
        raise NotImplementedError

    def build_webhook_response(
        self, outcome: CallbackOutcome
    ) -> WebhookResponse:  # pragma: no cover - task 13
        raise NotImplementedError


async def _seed_reference_and_tenant(role_urls: dict[str, str]) -> tuple[UUID, UUID]:
    """Seed the currency + plans (global) and a user + tenant (FK targets) as the
    table owner, bypassing RLS. Reference rows are truncated per test, so every
    billing test re-seeds them here."""
    tenant_id, user_id = uuid4(), uuid4()
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO currencies (code, exponent, name) VALUES ('UZS', 0, 'Som')")
            )
            # currency/period/trial_days rely on server defaults (UZS/month/0).
            await conn.execute(
                text(
                    "INSERT INTO plans (id, code, name, price_amount) "
                    "VALUES (:id, 'free', CAST(:name AS jsonb), 0)"
                ),
                {"id": uuid4(), "name": '{"ru": "Free", "uz": "Bepul"}'},
            )
            await conn.execute(
                text(
                    "INSERT INTO plans (id, code, name, price_amount) "
                    "VALUES (:id, 'pro', CAST(:name AS jsonb), :price)"
                ),
                {"id": uuid4(), "name": '{"ru": "Pro", "uz": "Pro"}', "price": PRO_PRICE},
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


@pytest.fixture
async def billing_ctx(role_urls: dict[str, str], _clean_db: None) -> TenantContext:
    tenant_id, user_id = await _seed_reference_and_tenant(role_urls)
    return TenantContext(
        tenant_id=tenant_id,
        actor=Actor(kind="user", id=str(user_id)),
        request_id="req-billing",
    )


def _billing(
    uow: SqlAlchemyUnitOfWork,
    bus: EventBus,
    ctx: TenantContext,
    settings: Settings,
    provider: FakePaymentProvider,
) -> BillingService:
    payments = PaymentService(uow, bus, ctx, providers={provider.code: provider}, settings=settings)
    return BillingService(uow, bus, ctx, payments=payments)


async def test_start_subscription_creates_pending_sub_and_payment(
    session_factory: async_sessionmaker[AsyncSession],
    billing_ctx: TenantContext,
    test_settings: Settings,
) -> None:
    bus, provider = EventBus(), FakePaymentProvider()
    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        billing = _billing(uow, bus, billing_ctx, test_settings, provider)
        checkout = await billing.start_subscription(PRO_PLAN, provider.code)

    assert checkout.provider == provider.code
    assert checkout.checkout_url
    assert provider.checkouts == [checkout.payment_id]

    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        subs = (await uow.session.execute(select(Subscription))).scalars().all()
        payments = (await uow.session.execute(select(Payment))).scalars().all()

    assert len(subs) == 1
    assert subs[0].status == "pending"
    assert len(payments) == 1
    payment = payments[0]
    assert payment.status == "created"
    assert payment.purpose == "subscription"
    assert payment.reference == str(subs[0].id)
    assert payment.amount == PRO_PRICE
    assert payment.id == checkout.payment_id


async def test_mark_succeeded_activates_subscription_in_same_tx(
    session_factory: async_sessionmaker[AsyncSession],
    billing_ctx: TenantContext,
    test_settings: Settings,
) -> None:
    bus, provider = EventBus(), FakePaymentProvider()
    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        billing = _billing(uow, bus, billing_ctx, test_settings, provider)
        checkout = await billing.start_subscription(PRO_PLAN, provider.code)
    payment_id = checkout.payment_id

    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        payments = PaymentService(
            uow, bus, billing_ctx, providers={provider.code: provider}, settings=test_settings
        )
        payment = await uow.session.get(Payment, payment_id)
        assert payment is not None
        await payments.mark_pending(payment, provider_txn_id="fake-txn-1")
        await payments.mark_succeeded(payment)
        subscription_id = UUID(payment.reference)

    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        payment = await uow.session.get(Payment, payment_id)
        subscription = await uow.session.get(Subscription, subscription_id)

    assert payment is not None
    assert payment.status == "succeeded"
    assert payment.paid_at is not None
    assert payment.provider_transaction_id == "fake-txn-1"
    assert subscription is not None
    assert subscription.status == "active"
    assert subscription.current_period_end > subscription.current_period_start


async def test_create_payment_is_idempotent_by_key(
    session_factory: async_sessionmaker[AsyncSession],
    billing_ctx: TenantContext,
    test_settings: Settings,
) -> None:
    bus, provider = EventBus(), FakePaymentProvider()
    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        payments = PaymentService(
            uow, bus, billing_ctx, providers={provider.code: provider}, settings=test_settings
        )
        first = await payments.create_payment(
            Money(1000, "UZS"),
            purpose="topup",
            reference="order-1",
            provider=provider.code,
            idempotency_key="idem-1",
        )
        second = await payments.create_payment(
            Money(1000, "UZS"),
            purpose="topup",
            reference="order-1",
            provider=provider.code,
            idempotency_key="idem-1",
        )

    assert first.payment_id == second.payment_id
    # The provider is invoked exactly once; the second call short-circuits.
    assert provider.checkouts == [first.payment_id]

    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        count = (await uow.session.execute(select(func.count()).select_from(Payment))).scalar_one()
    assert count == 1


async def test_second_live_subscription_conflicts(
    session_factory: async_sessionmaker[AsyncSession],
    billing_ctx: TenantContext,
    test_settings: Settings,
) -> None:
    bus, provider = EventBus(), FakePaymentProvider()
    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        billing = _billing(uow, bus, billing_ctx, test_settings, provider)
        await billing.start_subscription(PRO_PLAN, provider.code)

    with pytest.raises(ConflictError):
        async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
            billing = _billing(uow, bus, billing_ctx, test_settings, provider)
            await billing.start_subscription(PRO_PLAN, provider.code)

    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        subs = (
            await uow.session.execute(select(func.count()).select_from(Subscription))
        ).scalar_one()
    assert subs == 1


async def test_auto_subscribe_free_plan_is_active_and_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    billing_ctx: TenantContext,
    test_settings: Settings,
) -> None:
    bus, provider = EventBus(), FakePaymentProvider()
    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        billing = _billing(uow, bus, billing_ctx, test_settings, provider)
        await billing.auto_subscribe(FREE_PLAN)

    # Second call on a tenant that already has a live subscription is a no-op.
    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        billing = _billing(uow, bus, billing_ctx, test_settings, provider)
        await billing.auto_subscribe(FREE_PLAN)

    async with SqlAlchemyUnitOfWork(session_factory, context=billing_ctx) as uow:
        subs = (await uow.session.execute(select(Subscription))).scalars().all()
        payment_count = (
            await uow.session.execute(select(func.count()).select_from(Payment))
        ).scalar_one()

    assert len(subs) == 1
    assert subs[0].status == "active"  # trial_days == 0 -> immediately active
    assert payment_count == 0
    assert provider.checkouts == []  # zero-price plan never hits a provider
