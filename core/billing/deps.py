"""Request-scoped billing services (interfaces §3.3).

Built on the authenticated ServiceBundle so billing shares the request's unit of
work and tenant context; providers/settings/bus come from ``app.state`` (wired in
the lifespan) so core never imports app. The permission is checked on the route
(``require_permission``) and again in the services (the request's AccessService
is passed in).
"""

from dataclasses import dataclass

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle
from core.billing.service import BillingService, PaymentService


@dataclass(slots=True)
class BillingServices:
    billing: BillingService
    payments: PaymentService


async def billing_services(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> BillingServices:
    state = request.app.state
    payments = PaymentService(
        bundle.uow,
        state.bus,
        bundle.ctx,
        providers=state.payment_providers,
        settings=state.settings,
        access=bundle.access,
    )
    billing = BillingService(
        bundle.uow, state.bus, bundle.ctx, payments=payments, access=bundle.access
    )
    return BillingServices(billing=billing, payments=payments)
