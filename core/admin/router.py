"""Admin menu endpoint (interfaces §5.4).

``GET /api/admin/screens`` returns the menu — only the screens the current user
may see. Every screen's own router is mounted separately under
``/api/admin/{slug}`` by the composition root (app/admin_screens.py); this router
carries just the menu. Like every admin route it is gated by a permission
(``admin.screen:read``).
"""

from collections.abc import Sequence

from fastapi import APIRouter, Depends

from core.admin.permissions import SCREEN_READ
from core.admin.registry import ADMIN_PREFIX, admin_registry
from core.admin.schemas import AdminScreenInfo
from core.admin.service import AdminService
from core.auth.deps import ServiceBundle, authed_bundle, require_permission

router = APIRouter(prefix=ADMIN_PREFIX, tags=["admin"])


@router.get("/screens", dependencies=[Depends(require_permission(SCREEN_READ))])
async def list_screens(
    bundle: ServiceBundle = Depends(authed_bundle),
) -> Sequence[AdminScreenInfo]:
    service = AdminService(bundle.access, admin_registry)
    return await service.screens_for(bundle.user_id)
