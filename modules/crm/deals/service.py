"""DealService — the public interface of crm.deals (interfaces §1.2).

A sales opportunity moving through a configurable funnel (stages come from
``CRM_DEAL_STAGES``, so a client defines its own funnel; the template hardwires
none). A deal optionally links to a contact and/or company of crm.contacts —
validated through the sibling's public ContactsService (never reading its tables),
the same way commerce.cart prices through ProductService. When a contact/company
is deleted, the bus subscriber (subscribers.py) clears the link on every deal, so
no deal points at a person or company that no longer exists.

RBAC is checked here as well as on the route (PermissionGuard). Events are emitted
post-commit by the Service base. PATCH semantics: an argument left ``None`` leaves
the field unchanged (template-wide convention); a link is removed explicitly with
``clear_contact`` / ``clear_company`` (the route maps a JSON ``null`` onto them).
"""

from uuid import UUID

from sqlalchemy import ColumnElement

from core.auth.access_service import AccessService
from core.auth.guard import PermissionGuard
from modules.crm.contacts import ContactsService
from modules.crm.deals import permissions as perms
from modules.crm.deals.models import Deal
from modules.crm.deals.repository import DealRepository
from modules.crm.deals.schemas import DealDTO
from shared.context import TenantContext
from shared.errors import InvariantViolationError
from shared.events import EventBus
from shared.money import Money, currency_registry
from shared.pagination import Page, PageResult
from shared.service import Service, UnitOfWork


def _to_dto(deal: Deal) -> DealDTO:
    return DealDTO.model_validate(deal)


class DealService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        contacts: ContactsService,
        stages: tuple[str, ...],
        access: AccessService | None = None,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._repo = DealRepository(uow.session, ctx)
        self._contacts = contacts
        self._stages = stages
        self._guard = PermissionGuard(access, ctx)

    def _resolve_stage(self, stage: str | None) -> str:
        """A create-time stage: the given one (must be configured) or the first."""
        if stage is None:
            if not self._stages:
                raise InvariantViolationError("no deal stages configured (set CRM_DEAL_STAGES)")
            return self._stages[0]
        self._require_stage(stage)
        return stage

    def _require_stage(self, stage: str) -> None:
        if stage not in self._stages:
            raise InvariantViolationError(f"unknown deal stage: {stage!r}")

    async def _validate_links(self, contact_id: UUID | None, company_id: UUID | None) -> None:
        # Existence in the SAME tenant via the sibling's public interface (NotFound
        # for a foreign/missing id) — no cross-feature table read.
        if contact_id is not None:
            await self._contacts.ensure_contact(contact_id)
        if company_id is not None:
            await self._contacts.ensure_company(company_id)

    async def create(
        self,
        *,
        title: str,
        amount: int,
        currency: str = "UZS",
        stage: str | None = None,
        contact_id: UUID | None = None,
        company_id: UUID | None = None,
        notes: str | None = None,
    ) -> DealDTO:
        await self._guard.require(perms.DEAL_CREATE)
        Money(amount, currency)  # format + non-negative
        currency_registry.exponent(currency)  # currency must be known (else NotFound)
        resolved_stage = self._resolve_stage(stage)
        await self._validate_links(contact_id, company_id)
        deal = Deal(
            title=title,
            stage=resolved_stage,
            amount=amount,
            currency=currency,
            contact_id=contact_id,
            company_id=company_id,
            notes=notes,
        )
        await self._repo.add(deal)
        self.emit("crm.deal.created", {"deal_id": str(deal.id)})
        return _to_dto(deal)

    async def update(
        self,
        deal_id: UUID,
        *,
        title: str | None = None,
        amount: int | None = None,
        currency: str | None = None,
        stage: str | None = None,
        contact_id: UUID | None = None,
        company_id: UUID | None = None,
        notes: str | None = None,
        clear_contact: bool = False,
        clear_company: bool = False,
    ) -> DealDTO:
        await self._guard.require(perms.DEAL_UPDATE)
        deal = await self._repo.get_or_raise(deal_id)
        await self._validate_links(contact_id, company_id)
        previous_stage = deal.stage
        if title is not None:
            deal.title = title
        if amount is not None:
            deal.amount = amount
        if currency is not None:
            currency_registry.exponent(currency)
            deal.currency = currency
        if stage is not None:
            self._require_stage(stage)
            deal.stage = stage
        if contact_id is not None:
            deal.contact_id = contact_id
        elif clear_contact:
            deal.contact_id = None
        if company_id is not None:
            deal.company_id = company_id
        elif clear_company:
            deal.company_id = None
        if notes is not None:
            deal.notes = notes
        Money(deal.amount, deal.currency)  # re-validate the resulting money
        await self._session.flush()
        self.emit("crm.deal.updated", {"deal_id": str(deal.id)})
        if deal.stage != previous_stage:
            self.emit(
                "crm.deal.stage_changed",
                {"deal_id": str(deal.id), "from": previous_stage, "to": deal.stage},
            )
        return _to_dto(deal)

    async def get(self, deal_id: UUID) -> DealDTO:
        await self._guard.require(perms.DEAL_READ)
        return _to_dto(await self._repo.get_or_raise(deal_id))

    async def list(
        self,
        page: Page,
        *,
        stage: str | None = None,
        contact_id: UUID | None = None,
        company_id: UUID | None = None,
    ) -> PageResult[DealDTO]:
        await self._guard.require(perms.DEAL_READ)
        filters: list[ColumnElement[bool]] = []
        if stage is not None:
            filters.append(Deal.stage == stage)
        if contact_id is not None:
            filters.append(Deal.contact_id == contact_id)
        if company_id is not None:
            filters.append(Deal.company_id == company_id)
        result = await self._repo.find_paged(*filters, order_by=[Deal.created_at.desc()], page=page)
        return PageResult(
            items=[_to_dto(d) for d in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
        )

    async def delete(self, deal_id: UUID) -> None:
        await self._guard.require(perms.DEAL_DELETE)
        deal = await self._repo.get_or_raise(deal_id)
        await self._repo.delete(deal)
        self.emit("crm.deal.deleted", {"deal_id": str(deal_id)})

    # --- maintained by the bus subscribers (subscribers.py) ---

    async def unlink_contact(self, contact_id: UUID) -> int:
        """Clear a deleted contact from every deal of the tenant. Returns the count."""
        return await self._unlink(Deal.contact_id == contact_id, "contact_id")

    async def unlink_company(self, company_id: UUID) -> int:
        """Clear a deleted company from every deal of the tenant. Returns the count."""
        return await self._unlink(Deal.company_id == company_id, "company_id")

    async def _unlink(self, where: ColumnElement[bool], attr: str) -> int:
        deals = await self._repo.find(where, for_update=True)
        for deal in deals:
            setattr(deal, attr, None)
        await self._session.flush()
        for deal in deals:
            self.emit("crm.deal.updated", {"deal_id": str(deal.id)})
        return len(deals)
