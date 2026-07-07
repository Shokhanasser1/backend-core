"""Request-scoped ProductService (built on the authenticated ServiceBundle).

Shares the request's unit of work and tenant context; the bus comes from
app.state (wired in the lifespan) so the feature never imports app. The
permission check lives on the route (require_permission); this only assembles
the service.
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle
from modules.commerce.products.service import ProductService


async def product_service(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> ProductService:
    return ProductService(bundle.uow, request.app.state.bus, bundle.ctx)
