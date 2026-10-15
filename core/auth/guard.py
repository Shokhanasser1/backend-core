"""PermissionGuard — RBAC inside a service, the second line behind the route's
``require_permission`` (CLAUDE.md: the router is not the only line — jobs, bus
handlers, sibling features and tests reach services without a route).

Rules:
- a USER actor must hold the permission in the context tenant;
- an internal actor (system job, bus handler, payment integration) is trusted
  code and passes — it acts for the platform, not for a person;
- a user-facing service built WITHOUT an AccessService fails closed (403), so a
  call path that forgot to wire access never silently skips the check.
"""

from uuid import UUID

from core.auth.access_service import AccessService
from shared.context import TenantContext
from shared.errors import PermissionDeniedError


class PermissionGuard:
    def __init__(self, access: AccessService | None, ctx: TenantContext) -> None:
        self._access = access
        self._ctx = ctx

    async def require(self, permission: str) -> None:
        actor = self._ctx.actor
        if actor.kind != "user":
            return
        if self._access is None or actor.id is None:
            raise PermissionDeniedError(f"missing permission: {permission}")
        await self._access.require(UUID(actor.id), permission)
