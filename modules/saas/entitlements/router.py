"""Entitlement overview endpoint (/api/saas/entitlements). Every route carries one
permission marker (§5.2). Read-only: the tariff grid is a seeded reference table,
and sibling features enforce limits through EntitlementService, not over HTTP.
"""

from fastapi import APIRouter, Depends

from core.auth.deps import require_permission
from modules.saas.entitlements import permissions as perms
from modules.saas.entitlements.deps import entitlement_service
from modules.saas.entitlements.schemas import EntitlementsDTO
from modules.saas.entitlements.service import EntitlementService

router = APIRouter(prefix="/api/saas/entitlements", tags=["saas.entitlements"])


@router.get("/me", dependencies=[Depends(require_permission(perms.ENTITLEMENT_READ))])
async def my_entitlements(
    service: EntitlementService = Depends(entitlement_service),
) -> EntitlementsDTO:
    """The current tenant's effective flags and limits."""
    return await service.snapshot()
