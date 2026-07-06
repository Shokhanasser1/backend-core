"""AccessService — the reading side of RBAC (interfaces §3.1).

Owns the permission catalog (register_permissions) and answers permission
questions by delegating to a resolver (tenants owns the user-tenant-role link).
It never writes membership/role data — that stays in core/tenants.
"""

from typing import Protocol
from uuid import UUID

from core.auth.permissions import PermissionDef, permission_registry
from shared.errors import PermissionDeniedError


class PermissionResolver(Protocol):
    async def get_permission_codes(self, user_id: UUID) -> frozenset[str]: ...


class AccessService:
    def __init__(self, resolver: PermissionResolver) -> None:
        self._resolver = resolver

    async def list_permissions(self, user_id: UUID) -> frozenset[str]:
        return await self._resolver.get_permission_codes(user_id)

    async def has_permission(self, user_id: UUID, permission: str) -> bool:
        return permission in await self._resolver.get_permission_codes(user_id)

    async def require(self, user_id: UUID, permission: str) -> None:
        """Service-layer enforcement (second line after the router)."""
        if not await self.has_permission(user_id, permission):
            raise PermissionDeniedError(f"missing permission: {permission}")


def register_permissions(module: str, permissions: list[PermissionDef]) -> None:
    """Declared by modules at startup; the catalog backs the route validator."""
    permission_registry.register(module, permissions)
