"""PaymentService + BillingService (interfaces §3.3).

PaymentService is universal payment intake (used by billing and, in Phase 6, by
commerce). The payment state machine is owned here, not by adapters. Subscription
activation happens in the SAME transaction as the subscription payment's
finalization — "money taken, subscription inactive" is impossible by construction.
No refunds in v1 (OV-22).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from core.audit.service import AuditService
from core.billing.models import Payment, Plan, Subscription
from core.billing.periods import period_end
from core.billing.ports import PaymentProvider
from core.billing.repository import PaymentRepository, SubscriptionRepository
from core.billing.schemas import (
    CheckoutDTO,
    PaymentDTO,
    PaymentProviderInfo,
    PlanDTO,
    SubscriptionDTO,
)
from core.billing.state_machine import assert_transition
from shared.config import Settings
from shared.context import TenantContext
from shared.errors import ConflictError, InvariantViolationError, NotFoundError
from shared.events import EventBus
from shared.ids import new_uuid7
from shared.money import Money
from shared.service import Service, UnitOfWork

_LIVE_SUBSCRIPTION_STATES = ("pending", "trialing", "active", "past_due")


def _payment_dto(p: Payment) -> PaymentDTO:
    return PaymentDTO(
        id=p.id,
        status=p.status,
        amount=Money(amount=p.amount, currency=p.currency),
        purpose=p.purpose,
        reference=p.reference,
        provider=p.provider,
        paid_at=p.paid_at,
    )


def _plan_dto(p: Plan) -> PlanDTO:
    return PlanDTO(
        id=p.id,
        code=p.code,
        name=p.name,
        price=Money(amount=p.price_amount, currency=p.currency),
        period=p.period,
        trial_days=p.trial_days,
    )


def _subscription_dto(s: Subscription, plan_code: str) -> SubscriptionDTO:
    return SubscriptionDTO(
        id=s.id,
        plan_code=plan_code,
        status=s.status,
        current_period_end=s.current_period_end,
        cancel_at_period_end=s.cancel_at_period_end,
    )


def _payment_event_payload(p: Payment) -> dict[str, Any]:
    return {
        "payment_id": str(p.id),
        "amount": p.amount,
        "currency": p.currency,
        "purpose": p.purpose,
        "reference": p.reference,
        "provider": p.provider,
    }


class PaymentService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        providers: Mapping[str, PaymentProvider],
        settings: Settings,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._payments = PaymentRepository(uow.session, ctx)
        self._providers = providers
        self._settings = settings

    async def list_providers(self) -> Sequence[PaymentProviderInfo]:
        return [
            PaymentProviderInfo(code=code, title_key=f"payment.provider.{code}", enabled=True)
            for code in self._settings.enabled_payment_provider_list
        ]

    async def create_payment(
        self,
        amount: Money,
        *,
        purpose: str,
        reference: str,
        provider: str,
        idempotency_key: str,
        return_url: str | None = None,
        subscription_id: UUID | None = None,
    ) -> CheckoutDTO:
        if provider not in self._providers:
            raise NotFoundError(f"unknown or disabled payment provider: {provider}")

        existing = await self._payments.find(Payment.idempotency_key == idempotency_key, page=None)
        if existing:
            return self._checkout_from_metadata(existing[0])

        payment = Payment(
            id=new_uuid7(),
            subscription_id=subscription_id,
            purpose=purpose,
            reference=reference,
            amount=amount.amount,
            currency=amount.currency,
            status="created",
            provider=provider,
            idempotency_key=idempotency_key,
        )
        try:
            await self._payments.add(payment)
        except Exception as exc:  # unique (tenant, idempotency_key) race
            raise ConflictError("duplicate payment idempotency key") from exc

        checkout = await self._providers[provider].create_checkout(
            _payment_dto(payment), return_url
        )
        payment.payment_metadata = {
            **payment.payment_metadata,
            "checkout_url": checkout.checkout_url,
            "expires_at": checkout.expires_at.isoformat() if checkout.expires_at else None,
        }
        await self._session.flush()
        self.emit("billing.payment.created", _payment_event_payload(payment))
        return checkout

    def _checkout_from_metadata(self, payment: Payment) -> CheckoutDTO:
        meta = payment.payment_metadata
        expires_raw = meta.get("expires_at")
        return CheckoutDTO(
            payment_id=payment.id,
            provider=payment.provider,
            checkout_url=str(meta.get("checkout_url", "")),
            expires_at=datetime.fromisoformat(expires_raw) if expires_raw else None,
        )

    async def get_payment(self, payment_id: UUID) -> PaymentDTO:
        return _payment_dto(await self._payments.get_or_raise(payment_id))

    async def cancel_payment(self, payment_id: UUID) -> PaymentDTO:
        payment = await self._payments.get_or_raise(payment_id)
        assert_transition(payment.status, "canceled")
        await self._transition(payment, "canceled")
        return _payment_dto(payment)

    # --- finalization (called by webhook processing / expiry job) ---

    async def mark_pending(self, payment: Payment, *, provider_txn_id: str) -> None:
        assert_transition(payment.status, "pending")
        payment.provider_transaction_id = provider_txn_id
        payment.status = "pending"
        await self._session.flush()

    async def mark_succeeded(self, payment: Payment) -> None:
        assert_transition(payment.status, "succeeded")
        payment.status = "succeeded"
        payment.paid_at = datetime.now(UTC)
        await self._session.flush()
        if payment.purpose == "subscription":
            await self._activate_subscription(payment)
        event_id = self.emit(
            "billing.payment.succeeded",
            {**_payment_event_payload(payment), "paid_at": payment.paid_at.isoformat()},
        )
        await self._audit("billing.payment.succeeded", payment, event_id)

    async def mark_failed(self, payment: Payment, *, reason: str) -> None:
        assert_transition(payment.status, "failed")
        payment.status = "failed"
        payment.failure_code = reason
        await self._session.flush()
        event_id = self.emit(
            "billing.payment.failed", {**_payment_event_payload(payment), "reason": reason}
        )
        await self._audit("billing.payment.failed", payment, event_id)

    async def mark_canceled_by_provider(self, payment: Payment) -> None:
        assert_transition(payment.status, "canceled")
        await self._transition(payment, "canceled")

    async def mark_expired(self, payment: Payment) -> None:
        """Abandoned checkout swept by the expiry job (schema §2.3): created/pending
        -> expired. Commerce relies on billing.payment.expired to release a stuck
        order/reservation (interfaces §3.3)."""
        assert_transition(payment.status, "expired")
        await self._transition(payment, "expired")

    async def _transition(self, payment: Payment, target: str) -> None:
        payment.status = target
        await self._session.flush()
        event_id = self.emit(f"billing.payment.{target}", _payment_event_payload(payment))
        await self._audit(f"billing.payment.{target}", payment, event_id)

    async def _activate_subscription(self, payment: Payment) -> None:
        subscription = await self._session.get(Subscription, UUID(payment.reference))
        if subscription is None:
            raise NotFoundError("subscription for payment not found")
        plan = await self._session.get(Plan, subscription.plan_id)
        now = datetime.now(UTC)
        subscription.status = "active"
        subscription.current_period_start = now
        subscription.current_period_end = period_end(now, plan.period if plan else "month")
        await self._session.flush()
        self.emit(
            "billing.subscription.activated",
            {
                "subscription_id": str(subscription.id),
                "plan_code": plan.code if plan else "",
                "current_period_end": subscription.current_period_end.isoformat(),
            },
        )

    async def _audit(self, action: str, payment: Payment, event_id: UUID) -> None:
        await AuditService(self._session, self.ctx).record(
            action=action, object_type="payment", object_id=str(payment.id), event_id=event_id
        )


class BillingService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        payments: PaymentService,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._subscriptions = SubscriptionRepository(uow.session, ctx)
        self._payments = payments

    async def list_plans(self) -> Sequence[PlanDTO]:
        rows = (
            (
                await self._session.execute(
                    select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_amount)
                )
            )
            .scalars()
            .all()
        )
        return [_plan_dto(p) for p in rows]

    async def get_subscription(self) -> SubscriptionDTO | None:
        sub = await self._subscriptions.find(
            Subscription.status.in_(_LIVE_SUBSCRIPTION_STATES), page=None
        )
        if not sub:
            return None
        plan = await self._session.get(Plan, sub[0].plan_id)
        return _subscription_dto(sub[0], plan.code if plan else "")

    async def _plan_by_code(self, plan_code: str) -> Plan:
        plan = (
            await self._session.execute(
                select(Plan).where(Plan.code == plan_code, Plan.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if plan is None:
            raise NotFoundError(f"plan not found: {plan_code}")
        return plan

    async def start_subscription(self, plan_code: str, provider: str) -> CheckoutDTO:
        plan = await self._plan_by_code(plan_code)
        now = datetime.now(UTC)
        subscription = Subscription(
            id=new_uuid7(),
            plan_id=plan.id,
            status="pending",
            price_amount=plan.price_amount,
            currency=plan.currency,
            current_period_start=now,
            current_period_end=period_end(now, plan.period),
        )
        try:
            await self._subscriptions.add(subscription)
        except Exception as exc:  # partial-unique: one live subscription per tenant
            raise ConflictError("a live subscription already exists") from exc

        return await self._payments.create_payment(
            Money(amount=plan.price_amount, currency=plan.currency),
            purpose="subscription",
            reference=str(subscription.id),
            provider=provider,
            idempotency_key=f"subscription:{subscription.id}",
            subscription_id=subscription.id,
        )

    async def cancel_subscription(self) -> SubscriptionDTO:
        sub = await self._subscriptions.find(
            Subscription.status.in_(("trialing", "active", "past_due")), page=None
        )
        if not sub:
            raise NotFoundError("no active subscription")
        subscription = sub[0]
        subscription.cancel_at_period_end = True
        subscription.canceled_at = datetime.now(UTC)
        await self._session.flush()
        plan = await self._session.get(Plan, subscription.plan_id)
        self.emit(
            "billing.subscription.canceled",
            {"subscription_id": str(subscription.id), "plan_code": plan.code if plan else ""},
        )
        return _subscription_dto(subscription, plan.code if plan else "")

    async def auto_subscribe(self, plan_code: str) -> None:
        """Create a free/trial subscription for a new tenant (OV-21). Activates
        immediately for a zero-price plan (no payment)."""
        plan = await self._plan_by_code(plan_code)
        if plan.price_amount != 0:
            raise InvariantViolationError("auto-subscribe requires a zero-price plan")
        existing = await self._subscriptions.find(
            Subscription.status.in_(_LIVE_SUBSCRIPTION_STATES), page=None
        )
        if existing:
            return  # already has a live subscription — idempotent
        now = datetime.now(UTC)
        status = "trialing" if plan.trial_days > 0 else "active"
        subscription = Subscription(
            id=new_uuid7(),
            plan_id=plan.id,
            status=status,
            price_amount=plan.price_amount,
            currency=plan.currency,
            current_period_start=now,
            current_period_end=period_end(now, plan.period),
        )
        await self._subscriptions.add(subscription)
        self.emit(
            "billing.subscription.activated",
            {
                "subscription_id": str(subscription.id),
                "plan_code": plan.code,
                "current_period_end": subscription.current_period_end.isoformat(),
            },
        )
