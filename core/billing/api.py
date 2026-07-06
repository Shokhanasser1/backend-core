"""Authenticated billing HTTP endpoints (/api/billing). Every route carries one
permission marker (interfaces §3.3/§5.2). Payment webhook routes are separate
(router.py) — they are public and answer in the provider dialect.
"""

from collections.abc import Sequence

from fastapi import APIRouter, Depends, status

from core.auth.deps import require_permission
from core.billing import permissions as perms
from core.billing.deps import BillingServices, billing_services
from core.billing.schemas import (
    CheckoutDTO,
    PaymentProviderInfo,
    PlanDTO,
    StartSubscriptionIn,
    SubscriptionDTO,
)

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/plans", dependencies=[Depends(require_permission(perms.PLAN_READ))])
async def list_plans(
    services: BillingServices = Depends(billing_services),
) -> Sequence[PlanDTO]:
    return await services.billing.list_plans()


@router.get("/providers", dependencies=[Depends(require_permission(perms.PLAN_READ))])
async def list_providers(
    services: BillingServices = Depends(billing_services),
) -> Sequence[PaymentProviderInfo]:
    return await services.payments.list_providers()


@router.get("/subscription", dependencies=[Depends(require_permission(perms.SUBSCRIPTION_READ))])
async def get_subscription(
    services: BillingServices = Depends(billing_services),
) -> SubscriptionDTO | None:
    return await services.billing.get_subscription()


@router.post(
    "/subscription",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perms.SUBSCRIPTION_MANAGE))],
)
async def start_subscription(
    body: StartSubscriptionIn, services: BillingServices = Depends(billing_services)
) -> CheckoutDTO:
    return await services.billing.start_subscription(body.plan_code, body.provider)


@router.post(
    "/subscription/cancel",
    dependencies=[Depends(require_permission(perms.SUBSCRIPTION_MANAGE))],
)
async def cancel_subscription(
    services: BillingServices = Depends(billing_services),
) -> SubscriptionDTO:
    return await services.billing.cancel_subscription()
