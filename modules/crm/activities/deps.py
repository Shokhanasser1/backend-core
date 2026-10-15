"""Request-scoped ActivityService (built on the authenticated ServiceBundle).

Shares the request's unit of work and tenant context. The siblings' public services
(ContactsService, DealService) are built on the SAME bundle, so link validation runs
in the same transaction. The configured types come from app.state.settings
(CRM_ACTIVITY_TYPES); the bus from app.state — the feature never imports app. The
permission is checked on the route (require_permission) and again in the service
(the request's AccessService is passed in).
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle
from modules.crm.activities.service import ActivityService
from modules.crm.contacts import ContactsService
from modules.crm.deals import DealService


async def activity_service(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> ActivityService:
    state = request.app.state
    contacts = ContactsService(bundle.uow, state.bus, bundle.ctx, access=bundle.access)
    deals = DealService(
        bundle.uow,
        state.bus,
        bundle.ctx,
        contacts=contacts,
        stages=state.settings.crm_deal_stage_list,
        access=bundle.access,
    )
    return ActivityService(
        bundle.uow,
        state.bus,
        bundle.ctx,
        contacts=contacts,
        deals=deals,
        types=state.settings.crm_activity_type_list,
        access=bundle.access,
    )
