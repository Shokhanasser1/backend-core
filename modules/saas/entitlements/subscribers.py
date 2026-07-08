"""saas.entitlements <- billing: keep the tenant's active plan in step (§2.6).

Billing owns money and subscriptions; it never knows about entitlements. This
feature reacts to billing's PUBLIC events and maps the active plan_code onto the
tenant's entitlement row. Reliable subscribers keyed to explicit event names
(features may not use wildcards — §1.1); they run inside the worker's UoW +
tenant context (reconstructed from the envelope), so writes pass RLS. Registered
by importing this module in the feature's ``install()`` (web) and the worker's
module install, so the handlers are resolvable in the arq worker.
"""

from datetime import datetime

from modules.saas.entitlements.service import EntitlementService
from shared.events import EventEnvelope, bus
from shared.handler_runtime import current_handler_runtime


@bus.subscribe("billing.subscription.activated", reliable=True)
async def on_subscription_activated(event: EventEnvelope) -> None:
    plan_code = str(event.payload.get("plan_code") or "")
    if not plan_code:
        return
    runtime = current_handler_runtime()
    if runtime.ctx.tenant_id is None:
        return  # activation always carries a tenant; guard defensively
    period_raw = event.payload.get("current_period_end")
    period_end = datetime.fromisoformat(str(period_raw)) if period_raw else None
    service = EntitlementService(runtime.uow, runtime.bus, runtime.ctx)
    await service.set_active_plan(plan_code, period_end)


@bus.subscribe("billing.subscription.canceled", reliable=True)
async def on_subscription_canceled(event: EventEnvelope) -> None:
    runtime = current_handler_runtime()
    if runtime.ctx.tenant_id is None:
        return
    service = EntitlementService(runtime.uow, runtime.bus, runtime.ctx)
    await service.mark_canceled()
