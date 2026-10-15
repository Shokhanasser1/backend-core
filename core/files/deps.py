"""Request-scoped FileService (built on the authenticated ServiceBundle).

Shares the request's unit of work and tenant context; the bus, storage backend
and settings come from app.state (wired in the lifespan) so core never imports
app. The permission is checked on the route (require_permission) and again in the
service (the request's AccessService is passed in).
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle
from core.files.service import FileService


async def file_service(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> FileService:
    state = request.app.state
    return FileService(
        bundle.uow,
        state.bus,
        bundle.ctx,
        storage=state.file_storage,
        thumbnailer=state.file_thumbnailer,
        settings=state.settings,
        access=bundle.access,
    )
