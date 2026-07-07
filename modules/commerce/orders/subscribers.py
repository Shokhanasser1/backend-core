"""orders <- billing: mark orders paid/canceled by the payment outcome (§6.5).

orders owns the receipt semantics (its template) and reacts to billing's public
events; billing never knows about orders. Reliable subscribers keyed to explicit
event names (features may not use wildcards — §1.1). Registered by importing this
module in the feature's ``install()`` (web) and the worker's module install, so the
handlers are resolvable in the arq worker and the template is available to the
notification dispatcher.
"""

import logging
from pathlib import Path
from uuid import UUID

from core.auth.directory import UserDirectory
from core.notifications.registry import TemplateDef, register_templates, template_registry
from core.notifications.schemas import UserRecipient
from core.notifications.service import NotificationService
from modules.commerce.orders.service import OrderService
from shared.config import get_settings
from shared.encryption import SecretCipher
from shared.events import EventEnvelope, bus
from shared.handler_runtime import current_handler_runtime

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

ORDER_TEMPLATES = [
    TemplateDef("commerce.order_paid", ("email",), frozenset({"order_id", "amount", "currency"})),
]
register_templates("commerce", ORDER_TEMPLATES, _TEMPLATES_DIR)


def _is_order_payment(event: EventEnvelope) -> str | None:
    """The order id if this payment event belongs to a commerce order, else None."""
    if event.payload.get("purpose") != "commerce.order":
        return None
    reference = event.payload.get("reference")
    return str(reference) if reference else None


@bus.subscribe("billing.payment.succeeded", reliable=True)
async def mark_order_paid(event: EventEnvelope) -> None:
    order_id = _is_order_payment(event)
    if order_id is None:
        return
    runtime = current_handler_runtime()
    service = OrderService(runtime.uow, runtime.bus, runtime.ctx)
    payment_raw = event.payload.get("payment_id")
    order = await service.mark_paid(
        UUID(order_id), payment_id=UUID(str(payment_raw)) if payment_raw else None
    )
    if order is None:
        return
    # Receipt to the buyer (idempotent by dedup_key — at-least-once never doubles it).
    settings = get_settings()
    notifications = NotificationService(
        runtime.uow,
        runtime.bus,
        runtime.ctx,
        registry=template_registry,
        cipher=SecretCipher(settings.secret_encryption_key_list),
        directory=UserDirectory(runtime.uow.session),
    )
    await notifications.send(
        UserRecipient(order.customer_user_id),
        "commerce.order_paid",
        {"order_id": str(order.id), "amount": order.total_amount, "currency": order.currency},
        dedup_key=f"order_paid:{order.id}",
    )


async def _cancel_order(event: EventEnvelope) -> None:
    order_id = _is_order_payment(event)
    if order_id is None:
        return
    runtime = current_handler_runtime()
    service = OrderService(runtime.uow, runtime.bus, runtime.ctx)
    await service.cancel(UUID(order_id), reason=event.name)


@bus.subscribe("billing.payment.failed", reliable=True)
async def cancel_order_on_failure(event: EventEnvelope) -> None:
    await _cancel_order(event)


@bus.subscribe("billing.payment.canceled", reliable=True)
async def cancel_order_on_cancellation(event: EventEnvelope) -> None:
    await _cancel_order(event)


@bus.subscribe("billing.payment.expired", reliable=True)
async def cancel_order_on_expiry(event: EventEnvelope) -> None:
    await _cancel_order(event)
