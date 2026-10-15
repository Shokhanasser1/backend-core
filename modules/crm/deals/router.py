"""CRM pipeline endpoints (/api/crm/deals). Every route carries exactly one
permission marker (interfaces §5.2). Managed by tenant members via RBAC; delete is
owner/admin only (see permissions.py).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from core.auth.deps import require_permission
from modules.crm.deals import permissions as perms
from modules.crm.deals.deps import deal_service
from modules.crm.deals.schemas import CreateDealIn, DealChanges, DealDTO, UpdateDealIn
from modules.crm.deals.service import DealService
from shared.pagination import MAX_PAGE_LIMIT, Page, PageResult

router = APIRouter(prefix="/api/crm/deals", tags=["crm.deals"])


@router.get("", dependencies=[Depends(require_permission(perms.DEAL_READ))])
async def list_deals(
    service: DealService = Depends(deal_service),
    stage: str | None = Query(default=None),
    contact_id: UUID | None = Query(default=None),
    company_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> PageResult[DealDTO]:
    return await service.list(
        Page(limit=limit, offset=offset),
        stage=stage,
        contact_id=contact_id,
        company_id=company_id,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perms.DEAL_CREATE))],
)
async def create_deal(body: CreateDealIn, service: DealService = Depends(deal_service)) -> DealDTO:
    return await service.create(
        title=body.title,
        amount=body.amount,
        currency=body.currency,
        stage=body.stage,
        contact_id=body.contact_id,
        company_id=body.company_id,
        notes=body.notes,
    )


@router.get("/{deal_id}", dependencies=[Depends(require_permission(perms.DEAL_READ))])
async def get_deal(deal_id: UUID, service: DealService = Depends(deal_service)) -> DealDTO:
    return await service.get(deal_id)


@router.patch("/{deal_id}", dependencies=[Depends(require_permission(perms.DEAL_UPDATE))])
async def update_deal(
    deal_id: UUID, body: UpdateDealIn, service: DealService = Depends(deal_service)
) -> DealDTO:
    # An explicit JSON null clears a link; an absent field leaves it unchanged.
    return await service.update(deal_id, DealChanges.from_request(body))


@router.delete(
    "/{deal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perms.DEAL_DELETE))],
)
async def delete_deal(deal_id: UUID, service: DealService = Depends(deal_service)) -> None:
    await service.delete(deal_id)
