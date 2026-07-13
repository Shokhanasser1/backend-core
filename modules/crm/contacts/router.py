"""CRM address-book endpoints (/api/crm). Every route carries exactly one
permission marker (interfaces §5.2). Managed by tenant members via RBAC; delete
is owner/admin only (see permissions.py).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from core.auth.deps import require_permission
from modules.crm.contacts import permissions as perms
from modules.crm.contacts.deps import contacts_service
from modules.crm.contacts.schemas import (
    CompanyDTO,
    ContactDTO,
    CreateCompanyIn,
    CreateContactIn,
    UpdateCompanyIn,
    UpdateContactIn,
)
from modules.crm.contacts.service import ContactsService
from shared.pagination import MAX_PAGE_LIMIT, Page, PageResult

router = APIRouter(prefix="/api/crm", tags=["crm.contacts"])


# --- companies -------------------------------------------------------------


@router.get("/companies", dependencies=[Depends(require_permission(perms.COMPANY_READ))])
async def list_companies(
    service: ContactsService = Depends(contacts_service),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> PageResult[CompanyDTO]:
    return await service.list_companies(Page(limit=limit, offset=offset))


@router.post(
    "/companies",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perms.COMPANY_CREATE))],
)
async def create_company(
    body: CreateCompanyIn, service: ContactsService = Depends(contacts_service)
) -> CompanyDTO:
    return await service.create_company(
        name=body.name, website=body.website, industry=body.industry, notes=body.notes
    )


@router.get(
    "/companies/{company_id}", dependencies=[Depends(require_permission(perms.COMPANY_READ))]
)
async def get_company(
    company_id: UUID, service: ContactsService = Depends(contacts_service)
) -> CompanyDTO:
    return await service.get_company(company_id)


@router.patch(
    "/companies/{company_id}", dependencies=[Depends(require_permission(perms.COMPANY_UPDATE))]
)
async def update_company(
    company_id: UUID,
    body: UpdateCompanyIn,
    service: ContactsService = Depends(contacts_service),
) -> CompanyDTO:
    return await service.update_company(
        company_id,
        name=body.name,
        website=body.website,
        industry=body.industry,
        notes=body.notes,
    )


@router.delete(
    "/companies/{company_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perms.COMPANY_DELETE))],
)
async def delete_company(
    company_id: UUID, service: ContactsService = Depends(contacts_service)
) -> None:
    await service.delete_company(company_id)


# --- contacts --------------------------------------------------------------


@router.get("/contacts", dependencies=[Depends(require_permission(perms.CONTACT_READ))])
async def list_contacts(
    service: ContactsService = Depends(contacts_service),
    company_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> PageResult[ContactDTO]:
    return await service.list_contacts(Page(limit=limit, offset=offset), company_id=company_id)


@router.post(
    "/contacts",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perms.CONTACT_CREATE))],
)
async def create_contact(
    body: CreateContactIn, service: ContactsService = Depends(contacts_service)
) -> ContactDTO:
    return await service.create_contact(
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        phone=body.phone,
        position=body.position,
        company_id=body.company_id,
        notes=body.notes,
    )


@router.get(
    "/contacts/{contact_id}", dependencies=[Depends(require_permission(perms.CONTACT_READ))]
)
async def get_contact(
    contact_id: UUID, service: ContactsService = Depends(contacts_service)
) -> ContactDTO:
    return await service.get_contact(contact_id)


@router.patch(
    "/contacts/{contact_id}", dependencies=[Depends(require_permission(perms.CONTACT_UPDATE))]
)
async def update_contact(
    contact_id: UUID,
    body: UpdateContactIn,
    service: ContactsService = Depends(contacts_service),
) -> ContactDTO:
    return await service.update_contact(
        contact_id,
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        phone=body.phone,
        position=body.position,
        company_id=body.company_id,
        notes=body.notes,
    )


@router.delete(
    "/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perms.CONTACT_DELETE))],
)
async def delete_contact(
    contact_id: UUID, service: ContactsService = Depends(contacts_service)
) -> None:
    await service.delete_contact(contact_id)
