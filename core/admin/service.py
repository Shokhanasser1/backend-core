"""AdminService — the reading side of the admin scaffold (interfaces §3.6).

Composes the screen registry with RBAC: ``screens_for`` returns only the screens
whose gating permission the user holds in the current tenant, i.e. the menu. It
never mutates anything — admin actions live on the screens' own routers, each
gated by its own permission.
"""

from collections.abc import Sequence
from uuid import UUID

from core.admin.registry import ADMIN_PREFIX, AdminRegistry
from core.admin.schemas import AdminScreenInfo
from core.auth.access_service import AccessService


class AdminService:
    def __init__(self, access: AccessService, registry: AdminRegistry) -> None:
        self._access = access
        self._registry = registry

    async def screens_for(self, user_id: UUID) -> Sequence[AdminScreenInfo]:
        """The admin menu for a user: screens they have the gating permission for."""
        granted = await self._access.list_permissions(user_id)
        return [
            AdminScreenInfo(
                slug=screen.slug,
                title_key=screen.title_key,
                permission=screen.permission,
                path=f"{ADMIN_PREFIX}/{screen.slug}",
            )
            for screen in self._registry.screens()
            if screen.permission in granted
        ]
