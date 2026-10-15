"""Request-scoped ContactsService (built on the authenticated ServiceBundle).

Shares the request's unit of work and tenant context; the bus comes from
app.state (wired in the lifespan) so the feature never imports app. The
permission is checked on the route (require_permission) and again in the
service (the request's AccessService is passed in).
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle
from modules.crm.contacts.service import ContactsService


async def contacts_service(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> ContactsService:
    return ContactsService(bundle.uow, request.app.state.bus, bundle.ctx, access=bundle.access)
