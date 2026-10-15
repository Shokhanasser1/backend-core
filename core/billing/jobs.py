"""Platform billing jobs (interfaces §3.3, schema §2.3).

- Checkout-expiry sweep: an abandoned payment left in ``created``/``pending`` past
  the configured TTL is moved to ``expired`` and ``billing.payment.expired`` is
  published so a waiting commerce order/reservation can be released; a pending
  subscription checkout is dropped with it (the tenant's live plan stays).
- Period-end sweep: a live subscription whose period has ended is rolled forward
  (zero-price plan) or closed with a fallback to the free plan (paid plan — there
  is no recurring charge in v1).

Both are cross-tenant scans (app_maintenance, §3.4): the due rows are found once
without a tenant context, then EACH is handled in ITS OWN tenant context
(elevation §2.1) in a separate transaction — one slow/failed tenant never blocks
the rest. The row is re-read under ``FOR UPDATE`` inside that transaction, so a
callback that raced in between (created->succeeded) is waited for and seen, not
clobbered.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.billing.models import Payment, Subscription
from core.billing.service import LIVE_SUBSCRIPTION_STATES, BillingService, PaymentService
from shared.config import Settings
from shared.context import Actor, TenantContext
from shared.errors import InvariantViolationError
from shared.events import EventBus
from shared.service import SqlAlchemyUnitOfWork
from shared.system_repository import SystemRepository

logger = logging.getLogger(__name__)

# Bound each sweep so a large backlog is drained over several runs, not one long tx.
_SWEEP_BATCH = 500
_LIVE_STATES = ("created", "pending")


class _PaymentSystemRepo(SystemRepository[Payment]):
    model = Payment


class _SubscriptionSystemRepo(SystemRepository[Subscription]):
    model = Subscription


def _ctx(tenant_id: UUID | None, job: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, actor=Actor(kind="system", id=job), request_id=None)


async def expire_stale_checkouts(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    bus: EventBus,
    settings: Settings,
) -> int:
    """Expire abandoned checkouts older than the TTL. Returns the number expired."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.payment_checkout_ttl_seconds)

    async with SqlAlchemyUnitOfWork(
        maintenance_sessions, context=_ctx(None, "billing.expiry")
    ) as scan:
        stale = await _PaymentSystemRepo(scan.session).find(
            Payment.status.in_(_LIVE_STATES),
            Payment.created_at < cutoff,
            order_by=[Payment.created_at],
            limit=_SWEEP_BATCH,
        )
        targets = [(payment.id, payment.tenant_id) for payment in stale]

    expired = 0
    for payment_id, tenant_id in targets:
        tenant_ctx = _ctx(tenant_id, "billing.expiry")
        async with SqlAlchemyUnitOfWork(maintenance_sessions, context=tenant_ctx) as uow:
            payment = (
                await uow.session.execute(
                    select(Payment).where(Payment.id == payment_id).with_for_update()
                )
            ).scalar_one_or_none()
            if payment is None or payment.status not in _LIVE_STATES:
                continue  # a callback advanced it between scan and sweep — leave it
            payments = PaymentService(uow, bus, tenant_ctx, providers={}, settings=settings)
            try:
                await payments.mark_expired(payment)
                expired += 1
            except InvariantViolationError:
                logger.warning(
                    "checkout expiry skipped: invalid transition",
                    extra={"payment_id": str(payment_id), "status": payment.status},
                )
    if expired:
        logger.info("expired stale checkouts", extra={"count": expired})
    return expired


async def expire_ended_subscriptions(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    bus: EventBus,
    settings: Settings,
) -> int:
    """Roll forward or close live subscriptions whose period has ended. Returns
    the number of subscriptions acted on."""
    now = datetime.now(UTC)
    async with SqlAlchemyUnitOfWork(
        maintenance_sessions, context=_ctx(None, "billing.period_end")
    ) as scan:
        due = await _SubscriptionSystemRepo(scan.session).find(
            Subscription.status.in_(LIVE_SUBSCRIPTION_STATES),
            Subscription.current_period_end <= now,
            order_by=[Subscription.current_period_end],
            limit=_SWEEP_BATCH,
        )
        targets = [(sub.id, sub.tenant_id) for sub in due]

    fallback = settings.billing_default_plan_code if settings.billing_auto_subscribe else None
    acted = 0
    for subscription_id, tenant_id in targets:
        tenant_ctx = _ctx(tenant_id, "billing.period_end")
        async with SqlAlchemyUnitOfWork(maintenance_sessions, context=tenant_ctx) as uow:
            payments = PaymentService(uow, bus, tenant_ctx, providers={}, settings=settings)
            billing = BillingService(uow, bus, tenant_ctx, payments=payments)
            if await billing.close_ended_period(subscription_id, fallback_plan_code=fallback):
                acted += 1
    if acted:
        logger.info("subscription periods processed", extra={"count": acted})
    return acted
