"""Platform billing jobs (interfaces §3.3, schema §2.3).

The checkout-expiry sweep: an abandoned payment left in ``created``/``pending``
past the configured TTL is moved to ``expired`` and ``billing.payment.expired`` is
published so a waiting commerce order/reservation can be released (Phase 6).

It is a cross-tenant scan (app_maintenance, §3.4): the stale rows are found once
without a tenant context, then EACH is expired in ITS OWN tenant context
(elevation §2.1) in a separate transaction — one slow/failed tenant never blocks
the rest, and the payment status is re-checked inside the tx so a callback that
raced in between (created->succeeded) is not clobbered.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.billing.models import Payment
from core.billing.service import PaymentService
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


def _system_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=None, actor=Actor(kind="system", id="billing.expiry"), request_id=None
    )


async def expire_stale_checkouts(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    bus: EventBus,
    settings: Settings,
) -> int:
    """Expire abandoned checkouts older than the TTL. Returns the number expired."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.payment_checkout_ttl_seconds)

    async with SqlAlchemyUnitOfWork(maintenance_sessions, context=_system_ctx()) as scan:
        stale = await _PaymentSystemRepo(scan.session).find(
            Payment.status.in_(_LIVE_STATES),
            Payment.created_at < cutoff,
            order_by=[Payment.created_at],
            limit=_SWEEP_BATCH,
        )
        targets = [(payment.id, payment.tenant_id) for payment in stale]

    expired = 0
    for payment_id, tenant_id in targets:
        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            actor=Actor(kind="system", id="billing.expiry"),
            request_id=None,
        )
        async with SqlAlchemyUnitOfWork(maintenance_sessions, context=tenant_ctx) as uow:
            payment = await uow.session.get(Payment, payment_id)
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
