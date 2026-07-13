"""Request-scoped ContactsService (built on the authenticated ServiceBundle).

Shares the request's unit of work and tenant context; the bus comes from
app.state (wired in the lifespan) so the feature never imports app. The
permission check lives on the route (require_permission); this only assembles
the service.
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle
from modules.crm.contacts.service import ContactsService


async def contacts_service(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> ContactsService:
    return ContactsService(bundle.uow, request.app.state.bus, bundle.ctx)
