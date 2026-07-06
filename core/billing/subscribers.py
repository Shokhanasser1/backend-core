"""Billing event subscribers (interfaces §2.6, OV-21).

Auto-subscribe a new tenant to the free/trial plan on ``tenants.tenant.created``.
The tenant is created in user scope (no tenant context yet), so the envelope's
``tenant_id`` is None and the new tenant id is read from the event payload; the
handler then works in that tenant's context (app_user, reliable) — a normal
tenant write, not a maintenance one. Gated by ``billing_auto_subscribe``.

Registered by importing this module (done via ``core/subscribers.py`` in both the
web and worker processes).
"""

import logging
from uuid import UUID

from core.billing.service import BillingService, PaymentService
from shared.config import get_settings
from shared.context import Actor, TenantContext
from shared.db import apply_tenant_context
from shared.errors import DomainError
from shared.events import EventEnvelope, bus
from shared.handler_runtime import current_handler_runtime

logger = logging.getLogger(__name__)


@bus.subscribe("tenants.tenant.created", reliable=True)
async def auto_subscribe_new_tenant(event: EventEnvelope) -> None:
    settings = get_settings()
    if not settings.billing_auto_subscribe:
        return
    raw_tenant_id = event.payload.get("tenant_id")
    if not raw_tenant_id:
        return

    runtime = current_handler_runtime()
    ctx = TenantContext(
        tenant_id=UUID(str(raw_tenant_id)),
        actor=Actor(kind="system", id="billing.auto_subscribe"),
        request_id=None,
    )
    # Elevate the dispatcher's transaction into the new tenant so the subscription
    # insert passes RLS (the envelope carried no tenant context).
    await apply_tenant_context(runtime.uow.session, ctx)
    payments = PaymentService(runtime.uow, runtime.bus, ctx, providers={}, settings=settings)
    billing = BillingService(runtime.uow, runtime.bus, ctx, payments=payments)
    try:
        await billing.auto_subscribe(settings.billing_default_plan_code)
    except DomainError:
        # A misconfigured default plan (missing / priced) must not wedge tenant
        # creation retries; log and move on (a real free plan is a seed invariant).
        logger.exception(
            "auto-subscribe failed",
            extra={"tenant_id": str(ctx.tenant_id), "plan": settings.billing_default_plan_code},
        )
