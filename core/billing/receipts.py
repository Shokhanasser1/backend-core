"""Billing -> notifications: payment/subscription receipts (Phase 3, Task 16).

Billing owns the receipt SEMANTICS (templates + when to send); notifications is
pure infrastructure and never knows about billing. Registered by importing this
module in ``core/subscribers.py`` (both web and worker), so the templates are
available to the dispatcher and the reliable subscribers are resolvable.

The receipt is addressed to the tenant OWNER (UserRecipient -> email); the send is
idempotent by ``receipt:<id>`` so at-least-once delivery never duplicates it. The
subscriber runs as app_user in the event's tenant context (schema §3.4).
"""

import logging
from pathlib import Path

from core.auth.directory import UserDirectory
from core.notifications.registry import TemplateDef, register_templates, template_registry
from core.notifications.schemas import UserRecipient
from core.notifications.service import NotificationService
from core.tenants.directory import TenantDirectory
from shared.config import get_settings
from shared.encryption import SecretCipher
from shared.events import EventEnvelope, bus
from shared.handler_runtime import current_handler_runtime

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

BILLING_TEMPLATES = [
    TemplateDef("billing.payment_succeeded", ("email",), frozenset({"amount", "currency"})),
    TemplateDef("billing.subscription_activated", ("email",), frozenset({"plan_code"})),
]

# Register at import time (symmetric to the bus.subscribe decorators below), so
# both the web app and the arq worker can render these templates.
register_templates("billing", BILLING_TEMPLATES, _TEMPLATES_DIR)


async def _send_receipt(template: str, context: dict[str, object], dedup_key: str) -> None:
    runtime = current_handler_runtime()
    ctx = runtime.ctx
    if ctx.tenant_id is None:
        return  # tenant-scoped receipts only
    owner = await TenantDirectory(runtime.uow.session).get_owner_user_id(ctx.tenant_id)
    if owner is None:
        logger.warning(
            "receipt skipped: tenant has no owner", extra={"tenant_id": str(ctx.tenant_id)}
        )
        return
    settings = get_settings()
    service = NotificationService(
        runtime.uow,
        runtime.bus,
        ctx,
        registry=template_registry,
        cipher=SecretCipher(settings.secret_encryption_key_list),
        directory=UserDirectory(runtime.uow.session),
    )
    await service.send(UserRecipient(owner), template, context, dedup_key=dedup_key)


@bus.subscribe("billing.payment.succeeded", reliable=True)
async def send_payment_receipt(event: EventEnvelope) -> None:
    payment_id = str(event.payload.get("payment_id", ""))
    await _send_receipt(
        "billing.payment_succeeded",
        {
            "amount": event.payload.get("amount"),
            "currency": event.payload.get("currency"),
            "payment_id": payment_id,
        },
        dedup_key=f"receipt:payment:{payment_id}",
    )


@bus.subscribe("billing.subscription.activated", reliable=True)
async def send_subscription_activated(event: EventEnvelope) -> None:
    subscription_id = str(event.payload.get("subscription_id", ""))
    await _send_receipt(
        "billing.subscription_activated",
        {"plan_code": event.payload.get("plan_code"), "subscription_id": subscription_id},
        dedup_key=f"receipt:subscription_activated:{subscription_id}",
    )
