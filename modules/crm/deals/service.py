"""DealService — the public interface of crm.deals (interfaces §1.2).

A sales opportunity moving through a configurable funnel (stages come from
``CRM_DEAL_STAGES``, so a client defines its own funnel; the template hardwires
none). A deal optionally links to a contact and/or company of crm.contacts —
validated through the sibling's public ContactsService (never reading its tables),
the same way commerce.cart prices through ProductService.

Events are emitted post-commit by the Service base. PATCH semantics: an argument
left ``None`` leaves the field unchanged (template-wide convention).
"""

from uuid import UUID

from sqlalchemy import ColumnElement

from modules.crm.contacts import ContactsService
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
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._repo = DealRepository(uow.session, ctx)
        self._contacts = contacts
        self._stages = stages

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
            await self._contacts.get_contact(contact_id)
        if company_id is not None:
            await self._contacts.get_company(company_id)

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
    ) -> DealDTO:
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
        if company_id is not None:
            deal.company_id = company_id
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
        return _to_dto(await self._repo.get_or_raise(deal_id))

    async def list(
        self,
        page: Page,
        *,
        stage: str | None = None,
        contact_id: UUID | None = None,
        company_id: UUID | None = None,
    ) -> PageResult[DealDTO]:
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
        deal = await self._repo.get_or_raise(deal_id)
        await self._repo.delete(deal)
        self.emit("crm.deal.deleted", {"deal_id": str(deal_id)})
