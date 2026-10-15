"""Request-scoped DealService (built on the authenticated ServiceBundle).

Shares the request's unit of work and tenant context. The sibling ContactsService
(public interface of crm.contacts) is built on the SAME bundle so link validation
runs in the same transaction. The configured funnel comes from app.state.settings
(CRM_DEAL_STAGES); the bus comes from app.state — the feature never imports app.
The permission is checked on the route (require_permission) and again in the
service (the request's AccessService is passed in).
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle
from modules.crm.contacts import ContactsService
from modules.crm.deals.service import DealService


async def deal_service(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> DealService:
    contacts = ContactsService(bundle.uow, request.app.state.bus, bundle.ctx, access=bundle.access)
    return DealService(
        bundle.uow,
        request.app.state.bus,
        bundle.ctx,
        contacts=contacts,
        stages=request.app.state.settings.crm_deal_stage_list,
        access=bundle.access,
    )
