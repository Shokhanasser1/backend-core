"""CRM activity endpoints (/api/crm/activities). Every route carries exactly one
permission marker (interfaces §5.2). Members work with activities (read/create/
update, incl. complete/reopen); delete is owner/admin only (see permissions.py).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from core.auth.deps import require_permission
from modules.crm.activities import permissions as perms
from modules.crm.activities.deps import activity_service
from modules.crm.activities.schemas import (
    ActivityChanges,
    ActivityDTO,
    ActivityQuery,
    CreateActivityIn,
    UpdateActivityIn,
)
from modules.crm.activities.service import ActivityService
from shared.pagination import MAX_PAGE_LIMIT, Page, PageResult

router = APIRouter(prefix="/api/crm/activities", tags=["crm.activities"])


class ActivityListParams(ActivityQuery):
    """The list's query string: the filters plus pagination (FastAPI expands a
    query model only when it is the route's single query parameter)."""

    limit: int = Field(default=50, ge=1, le=MAX_PAGE_LIMIT)
    offset: int = Field(default=0, ge=0)


@router.get("", dependencies=[Depends(require_permission(perms.ACTIVITY_READ))])
async def list_activities(
    params: Annotated[ActivityListParams, Query()],
    service: ActivityService = Depends(activity_service),
) -> PageResult[ActivityDTO]:
    filters = ActivityQuery(**params.model_dump(exclude={"limit", "offset"}))
    return await service.list(Page(limit=params.limit, offset=params.offset), filters)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perms.ACTIVITY_CREATE))],
)
async def create_activity(
    body: CreateActivityIn, service: ActivityService = Depends(activity_service)
) -> ActivityDTO:
    return await service.create(body)


@router.get("/{activity_id}", dependencies=[Depends(require_permission(perms.ACTIVITY_READ))])
async def get_activity(
    activity_id: UUID, service: ActivityService = Depends(activity_service)
) -> ActivityDTO:
    return await service.get(activity_id)


@router.patch("/{activity_id}", dependencies=[Depends(require_permission(perms.ACTIVITY_UPDATE))])
async def update_activity(
    activity_id: UUID, body: UpdateActivityIn, service: ActivityService = Depends(activity_service)
) -> ActivityDTO:
    # An explicit JSON null clears a link / the due date; an absent field is unchanged.
    return await service.update(activity_id, ActivityChanges.from_request(body))


@router.post(
    "/{activity_id}/complete",
    dependencies=[Depends(require_permission(perms.ACTIVITY_UPDATE))],
)
async def complete_activity(
    activity_id: UUID, service: ActivityService = Depends(activity_service)
) -> ActivityDTO:
    return await service.complete(activity_id)


@router.post(
    "/{activity_id}/reopen",
    dependencies=[Depends(require_permission(perms.ACTIVITY_UPDATE))],
)
async def reopen_activity(
    activity_id: UUID, service: ActivityService = Depends(activity_service)
) -> ActivityDTO:
    return await service.reopen(activity_id)


@router.delete(
    "/{activity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perms.ACTIVITY_DELETE))],
)
async def delete_activity(
    activity_id: UUID, service: ActivityService = Depends(activity_service)
) -> None:
    await service.delete(activity_id)
