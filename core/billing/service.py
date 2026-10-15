"""PaymentService + BillingService (interfaces §3.3).

PaymentService is universal payment intake (used by billing and by commerce).
The payment state machine is owned here, not by adapters. Subscription
activation happens in the SAME transaction as the subscription payment's
finalization — "money taken, subscription inactive" is impossible by construction.
No refunds in v1 (OV-22).

Subscription lifecycle. A tenant has at most ONE live subscription (trialing/
active/past_due) and at most ONE pending (paid checkout in progress) — two
partial-unique indexes. An upgrade from the free plan therefore keeps the free
subscription live while the paid one is pending; payment success replaces the
live one with the paid one; a failed/canceled/abandoned checkout drops the
pending one and the free plan simply stays. A ended paid period (no recurring
charge in v1) closes the subscription and falls back to the free plan.

Callers that transition a payment hold its row lock (``SELECT ... FOR UPDATE``):
the webhook processor and the expiry sweep lock before reading the status, so a
concurrent transition is waited for and re-checked, never overwritten.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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
from shared.errors import ConflictError, DomainError, InvariantViolationError, NotFoundError
from shared.events import EventBus
from shared.ids import new_uuid7
from shared.money import Money
from shared.service import Service, UnitOfWork

logger = logging.getLogger(__name__)

LIVE_SUBSCRIPTION_STATES = ("trialing", "active", "past_due")
_MAX_RETURN_URL_LENGTH = 2048


def _meta_time(meta: Mapping[str, Any], key: str) -> datetime | None:
    raw = meta.get(key)
    return datetime.fromisoformat(raw) if isinstance(raw, str) and raw else None


def payment_dto(p: Payment) -> PaymentDTO:
    meta = p.payment_metadata or {}
    return PaymentDTO(
        id=p.id,
        status=p.status,
        amount=Money(amount=p.amount, currency=p.currency),
        purpose=p.purpose,
        reference=p.reference,
        provider=p.provider,
        paid_at=p.paid_at,
        pending_at=_meta_time(meta, "pending_at"),
        closed_at=_meta_time(meta, "closed_at"),
        failure_code=p.failure_code,
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


def _validate_return_url(url: str | None) -> None:
    """The redirect target is handed to the provider verbatim: it must be an
    absolute http(s) URL with nothing that could break out of a provider's own
    parameter encoding (Payme joins its params with ';'). Refused, never escaped."""
    if url is None:
        return
    unsafe = any(ch == ";" or ch.isspace() or not ch.isprintable() for ch in url)
    parts = urlsplit(url)
    if (
        unsafe
        or len(url) > _MAX_RETURN_URL_LENGTH
        or parts.scheme not in ("http", "https")
        or not parts.netloc
    ):
        raise InvariantViolationError("return_url must be an absolute http(s) URL")


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
        if amount.amount <= 0:
            raise InvariantViolationError("payment amount must be positive")
        _validate_return_url(return_url)
        request = (amount.amount, amount.currency, purpose, reference, provider)

        existing = await self._by_idempotency_key(idempotency_key)
        if existing is not None:
            return self._replay_checkout(existing, request)

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
            async with self._session.begin_nested():
                await self._payments.add(payment)
        except IntegrityError:
            # A concurrent request with the same key won the unique index (our
            # INSERT waited for its commit): answer from its row.
            winner = await self._by_idempotency_key(idempotency_key)
            if winner is None:
                raise
            return self._replay_checkout(winner, request)

        checkout = await self._providers[provider].create_checkout(payment_dto(payment), return_url)
        payment.payment_metadata = {
            **payment.payment_metadata,
            "checkout_url": checkout.checkout_url,
            "expires_at": checkout.expires_at.isoformat() if checkout.expires_at else None,
        }
        await self._session.flush()
        self.emit("billing.payment.created", _payment_event_payload(payment))
        return checkout

    async def _by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        rows = await self._payments.find(Payment.idempotency_key == idempotency_key, page=None)
        return rows[0] if rows else None

    def _replay_checkout(self, payment: Payment, request: tuple[object, ...]) -> CheckoutDTO:
        stored = (payment.amount, payment.currency, payment.purpose, payment.reference)
        if (*stored, payment.provider) != request:
            raise ConflictError("idempotency key already used for a different payment")
        meta = payment.payment_metadata
        expires_raw = meta.get("expires_at")
        return CheckoutDTO(
            payment_id=payment.id,
            provider=payment.provider,
            checkout_url=str(meta.get("checkout_url", "")),
            expires_at=datetime.fromisoformat(expires_raw) if expires_raw else None,
        )

    async def get_payment(self, payment_id: UUID) -> PaymentDTO:
        return payment_dto(await self._payments.get_or_raise(payment_id))

    async def cancel_payment(self, payment_id: UUID) -> PaymentDTO:
        payment = (
            await self._session.execute(
                select(Payment)
                .where(Payment.id == payment_id, Payment.tenant_id == self.ctx.tenant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if payment is None:
            raise NotFoundError(f"Payment {payment_id} not found")
        await self._close(payment, "canceled")
        return payment_dto(payment)

    # --- finalization (webhook processing / expiry job; caller holds the row lock) ---

    async def mark_pending(self, payment: Payment, *, provider_txn_id: str) -> None:
        assert_transition(payment.status, "pending")
        payment.provider_transaction_id = provider_txn_id
        payment.status = "pending"
        self._stamp(payment, "pending_at")
        await self._session.flush()

    async def mark_succeeded(self, payment: Payment) -> None:
        previous = payment.status
        assert_transition(previous, "succeeded")
        if previous == "expired":
            logger.warning(
                "late provider success after checkout expiry honored",
                extra={"payment_id": str(payment.id), "provider": payment.provider},
            )
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
        await self._close(payment, "failed", reason=reason)

    async def mark_canceled_by_provider(
        self, payment: Payment, *, reason: str | None = None
    ) -> None:
        await self._close(payment, "canceled", reason=reason)

    async def mark_expired(self, payment: Payment) -> None:
        """Abandoned checkout swept by the expiry job (schema §2.3): created/pending
        -> expired. Commerce relies on billing.payment.expired to release a stuck
        order/reservation (interfaces §3.3)."""
        await self._close(payment, "expired")

    async def _close(self, payment: Payment, target: str, *, reason: str | None = None) -> None:
        """Terminal non-success transition. A subscription checkout that closes
        drops its pending subscription; the tenant's live one is untouched."""
        assert_transition(payment.status, target)
        payment.status = target
        if reason is not None:
            payment.failure_code = reason
        self._stamp(payment, "closed_at")
        await self._session.flush()
        if payment.purpose == "subscription":
            await self._drop_pending_subscription(payment, target)
        payload = _payment_event_payload(payment)
        if target == "failed":
            payload["reason"] = reason
        event_id = self.emit(f"billing.payment.{target}", payload)
        await self._audit(f"billing.payment.{target}", payment, event_id)

    @staticmethod
    def _stamp(payment: Payment, key: str) -> None:
        # Reassign (not mutate): JSONB changes are tracked by identity.
        payment.payment_metadata = {**payment.payment_metadata, key: datetime.now(UTC).isoformat()}

    async def _locked_subscription(self, payment: Payment) -> Subscription | None:
        # Explicit tenant filter: webhook/job sessions run as app_maintenance,
        # whose RLS policy does not scope rows to the elevated tenant.
        subscription_id = payment.subscription_id or UUID(payment.reference)
        return (
            await self._session.execute(
                select(Subscription)
                .where(
                    Subscription.id == subscription_id,
                    Subscription.tenant_id == payment.tenant_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _drop_pending_subscription(self, payment: Payment, payment_status: str) -> None:
        subscription = await self._locked_subscription(payment)
        if subscription is None or subscription.status != "pending":
            return
        if payment_status == "expired":
            subscription.status = "expired"
        else:
            subscription.status = "canceled"
            subscription.canceled_at = datetime.now(UTC)
        await self._session.flush()

    async def _activate_subscription(self, payment: Payment) -> None:
        subscription = await self._locked_subscription(payment)
        if subscription is None:
            raise NotFoundError("subscription for payment not found")
        plan = await self._session.get(Plan, subscription.plan_id)
        now = datetime.now(UTC)
        start = now
        replaced = (
            (
                await self._session.execute(
                    select(Subscription)
                    .where(
                        Subscription.tenant_id == payment.tenant_id,
                        Subscription.status.in_(LIVE_SUBSCRIPTION_STATES),
                        Subscription.id != subscription.id,
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for old in replaced:
            if old.plan_id == subscription.plan_id and old.current_period_end > start:
                start = old.current_period_end  # paid early for the same plan: extend
            old.status = "canceled"
            old.canceled_at = now
        await self._session.flush()  # free the one-live slot before taking it

        subscription.status = "active"
        subscription.cancel_at_period_end = False
        subscription.current_period_start = start
        subscription.current_period_end = period_end(start, plan.period if plan else "month")
        await self._session.flush()
        for old in replaced:
            self.emit(
                "billing.subscription.replaced",
                {"subscription_id": str(old.id), "replaced_by": str(subscription.id)},
            )
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
        """The live subscription; a pending checkout only when nothing is live."""
        rows = await self._subscriptions.find(
            Subscription.status.in_((*LIVE_SUBSCRIPTION_STATES, "pending")), page=None
        )
        if not rows:
            return None
        sub = next((s for s in rows if s.status != "pending"), rows[0])
        plan = await self._session.get(Plan, sub.plan_id)
        return _subscription_dto(sub, plan.code if plan else "")

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
        """Open a paid checkout. The tenant's live (e.g. free) subscription stays
        live until the payment succeeds. Repeating the same checkout returns it;
        another plan/provider waits until the open one is paid or expires."""
        plan = await self._plan_by_code(plan_code)
        if plan.price_amount == 0:
            raise InvariantViolationError("a free plan is assigned automatically, not purchased")

        pending = await self._subscriptions.find(Subscription.status == "pending", page=None)
        if pending:
            if pending[0].plan_id != plan.id:
                raise ConflictError("a subscription checkout is already in progress")
            return await self._checkout_for(pending[0], provider)

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
            async with self._session.begin_nested():
                await self._subscriptions.add(subscription)
        except IntegrityError as exc:  # partial-unique: one pending checkout per tenant
            raise ConflictError("a subscription checkout is already in progress") from exc
        return await self._checkout_for(subscription, provider)

    async def _checkout_for(self, subscription: Subscription, provider: str) -> CheckoutDTO:
        # Keyed by the subscription: a repeat replays the same checkout, a
        # different provider for it is refused by the idempotency check.
        return await self._payments.create_payment(
            Money(amount=subscription.price_amount, currency=subscription.currency),
            purpose="subscription",
            reference=str(subscription.id),
            provider=provider,
            idempotency_key=f"subscription:{subscription.id}",
            subscription_id=subscription.id,
        )

    async def cancel_subscription(self) -> SubscriptionDTO:
        sub = await self._subscriptions.find(
            Subscription.status.in_(LIVE_SUBSCRIPTION_STATES), page=None
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
        """Create a free/trial subscription for a tenant without a live one (OV-21:
        new tenants; also the fallback when a paid period ends). Activates
        immediately for a zero-price plan (no payment)."""
        plan = await self._plan_by_code(plan_code)
        if plan.price_amount != 0:
            raise InvariantViolationError("auto-subscribe requires a zero-price plan")
        existing = await self._subscriptions.find(
            Subscription.status.in_(LIVE_SUBSCRIPTION_STATES), page=None
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

    async def close_ended_period(
        self, subscription_id: UUID, *, fallback_plan_code: str | None
    ) -> bool:
        """Period-end step for one subscription (sweep job, its own tenant
        transaction). Returns True if it acted. A zero-price plan rolls forward —
        free never lapses. Anything else closes (no recurring charge in v1):
        ``canceled`` if cancellation was requested, else ``expired``; the tenant
        then falls back to ``fallback_plan_code`` (None = no fallback)."""
        subscription = (
            await self._session.execute(
                select(Subscription)
                .where(
                    Subscription.id == subscription_id,
                    Subscription.tenant_id == self.ctx.tenant_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if (
            subscription is None
            or subscription.status not in LIVE_SUBSCRIPTION_STATES
            or subscription.current_period_end > now
        ):
            return False  # renewed or closed between the scan and this step
        plan = await self._session.get(Plan, subscription.plan_id)

        if plan is not None and plan.price_amount == 0 and not subscription.cancel_at_period_end:
            while subscription.current_period_end <= now:
                subscription.current_period_start = subscription.current_period_end
                subscription.current_period_end = period_end(
                    subscription.current_period_start, plan.period
                )
            subscription.status = "active"
            await self._session.flush()
            return True

        subscription.status = "canceled" if subscription.cancel_at_period_end else "expired"
        if subscription.canceled_at is None and subscription.cancel_at_period_end:
            subscription.canceled_at = now
        await self._session.flush()
        self.emit(
            "billing.subscription.ended",
            {
                "subscription_id": str(subscription.id),
                "plan_code": plan.code if plan else "",
                "status": subscription.status,
            },
        )
        if fallback_plan_code:
            try:
                await self.auto_subscribe(fallback_plan_code)
            except DomainError:
                # A misconfigured fallback plan must not keep an ended paid
                # subscription alive; log it, the closure itself stands.
                logger.exception(
                    "fallback subscription failed",
                    extra={"tenant_id": str(self.ctx.tenant_id), "plan": fallback_plan_code},
                )
        return True
