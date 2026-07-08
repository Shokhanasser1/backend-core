"""Usage overview endpoint (/api/saas/usage). Every route carries one permission
marker (§5.2). Read-only: recording is a server-side service call
(MeteringService.record), not an HTTP action.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query

from core.auth.deps import require_permission
from modules.saas.metering import permissions as perms
from modules.saas.metering.deps import metering_service
from modules.saas.metering.schemas import UsageWindowDTO
from modules.saas.metering.service import MeteringService

router = APIRouter(prefix="/api/saas/usage", tags=["saas.metering"])


@router.get("/me", dependencies=[Depends(require_permission(perms.USAGE_READ))])
async def my_usage(
    service: MeteringService = Depends(metering_service),
    since: date | None = Query(default=None),
    until: date | None = Query(default=None),
) -> UsageWindowDTO:
    """The current tenant's per-metric usage totals over an optional day window."""
    metrics = await service.summary(since=since, until=until)
    return UsageWindowDTO(since=since, until=until, metrics=metrics)
