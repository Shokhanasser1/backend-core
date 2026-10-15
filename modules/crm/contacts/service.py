"""ContactsService — the public interface of crm.contacts (interfaces §1.2).

Manages two entities of one feature: companies (organizations) and contacts
(people). A contact may belong to a company in the same tenant; the link is
validated through the tenant-scoped CompanyRepository, so a foreign/missing
company id is a 404 (never a cross-tenant leak) before the FK ever sees it.

Siblings (deals, tasks — later) read people/companies through this service, never
crm_contacts/crm_companies directly; for link validation they use
``ensure_contact`` / ``ensure_company`` (existence only, no permission of the
address book needed). Every other method is RBAC-checked here as well as on the
route (PermissionGuard). Events are emitted post-commit by the Service base.
PATCH semantics: an argument left ``None`` leaves the field unchanged
(template-wide convention, see schemas).
"""

from uuid import UUID

from core.auth.access_service import AccessService
from core.auth.guard import PermissionGuard
from modules.crm.contacts import permissions as perms
from modules.crm.contacts.models import Company, Contact
from modules.crm.contacts.repository import CompanyRepository, ContactRepository
from modules.crm.contacts.schemas import CompanyDTO, ContactDTO
from shared.context import TenantContext
from shared.events import EventBus
from shared.pagination import Page, PageResult
from shared.service import Service, UnitOfWork


def _company_dto(company: Company) -> CompanyDTO:
    return CompanyDTO.model_validate(company)


def _contact_dto(contact: Contact) -> ContactDTO:
    return ContactDTO.model_validate(contact)


class ContactsService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        access: AccessService | None = None,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._companies = CompanyRepository(uow.session, ctx)
        self._contacts = ContactRepository(uow.session, ctx)
        self._guard = PermissionGuard(access, ctx)

    # --- sibling link validation (existence in the context tenant only) ----

    async def ensure_contact(self, contact_id: UUID) -> None:
        await self._contacts.get_or_raise(contact_id)

    async def ensure_company(self, company_id: UUID) -> None:
        await self._companies.get_or_raise(company_id)

    # --- companies ---------------------------------------------------------

    async def create_company(
        self,
        *,
        name: str,
        website: str | None = None,
        industry: str | None = None,
        notes: str | None = None,
    ) -> CompanyDTO:
        await self._guard.require(perms.COMPANY_CREATE)
        company = Company(name=name, website=website, industry=industry, notes=notes)
        await self._companies.add(company)
        self.emit("crm.company.created", {"company_id": str(company.id)})
        return _company_dto(company)

    async def update_company(
        self,
        company_id: UUID,
        *,
        name: str | None = None,
        website: str | None = None,
        industry: str | None = None,
        notes: str | None = None,
    ) -> CompanyDTO:
        await self._guard.require(perms.COMPANY_UPDATE)
        company = await self._companies.get_or_raise(company_id)
        if name is not None:
            company.name = name
        if website is not None:
            company.website = website
        if industry is not None:
            company.industry = industry
        if notes is not None:
            company.notes = notes
        await self._session.flush()
        self.emit("crm.company.updated", {"company_id": str(company.id)})
        return _company_dto(company)

    async def get_company(self, company_id: UUID) -> CompanyDTO:
        await self._guard.require(perms.COMPANY_READ)
        return _company_dto(await self._companies.get_or_raise(company_id))

    async def list_companies(self, page: Page) -> PageResult[CompanyDTO]:
        await self._guard.require(perms.COMPANY_READ)
        result = await self._companies.find_paged(order_by=[Company.name.asc()], page=page)
        return PageResult(
            items=[_company_dto(c) for c in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
        )

    async def delete_company(self, company_id: UUID) -> None:
        """Delete a company; its contacts are un-assigned (FK ON DELETE SET NULL)."""
        await self._guard.require(perms.COMPANY_DELETE)
        company = await self._companies.get_or_raise(company_id)
        await self._companies.delete(company)
        self.emit("crm.company.deleted", {"company_id": str(company_id)})

    # --- contacts ----------------------------------------------------------

    async def create_contact(
        self,
        *,
        first_name: str,
        last_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        position: str | None = None,
        company_id: UUID | None = None,
        notes: str | None = None,
    ) -> ContactDTO:
        await self._guard.require(perms.CONTACT_CREATE)
        if company_id is not None:
            await self._companies.get_or_raise(company_id)  # same-tenant existence (404 otherwise)
        contact = Contact(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            position=position,
            company_id=company_id,
            notes=notes,
        )
        await self._contacts.add(contact)
        self.emit("crm.contact.created", {"contact_id": str(contact.id)})
        return _contact_dto(contact)

    async def update_contact(
        self,
        contact_id: UUID,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        position: str | None = None,
        company_id: UUID | None = None,
        notes: str | None = None,
    ) -> ContactDTO:
        await self._guard.require(perms.CONTACT_UPDATE)
        contact = await self._contacts.get_or_raise(contact_id)
        if company_id is not None:
            await self._companies.get_or_raise(company_id)  # same-tenant existence (404 otherwise)
            contact.company_id = company_id
        if first_name is not None:
            contact.first_name = first_name
        if last_name is not None:
            contact.last_name = last_name
        if email is not None:
            contact.email = email
        if phone is not None:
            contact.phone = phone
        if position is not None:
            contact.position = position
        if notes is not None:
            contact.notes = notes
        await self._session.flush()
        self.emit("crm.contact.updated", {"contact_id": str(contact.id)})
        return _contact_dto(contact)

    async def get_contact(self, contact_id: UUID) -> ContactDTO:
        await self._guard.require(perms.CONTACT_READ)
        return _contact_dto(await self._contacts.get_or_raise(contact_id))

    async def list_contacts(
        self, page: Page, *, company_id: UUID | None = None
    ) -> PageResult[ContactDTO]:
        await self._guard.require(perms.CONTACT_READ)
        filters = [Contact.company_id == company_id] if company_id is not None else []
        result = await self._contacts.find_paged(
            *filters, order_by=[Contact.created_at.desc()], page=page
        )
        return PageResult(
            items=[_contact_dto(c) for c in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
        )

    async def delete_contact(self, contact_id: UUID) -> None:
        await self._guard.require(perms.CONTACT_DELETE)
        contact = await self._contacts.get_or_raise(contact_id)
        await self._contacts.delete(contact)
        self.emit("crm.contact.deleted", {"contact_id": str(contact_id)})
