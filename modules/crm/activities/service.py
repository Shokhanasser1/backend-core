"""ActivityService — the public interface of crm.activities (interfaces §1.2).

One entity for the plan and the history of touches: a call, a meeting, a task or an
email (types are configuration — ``CRM_ACTIVITY_TYPES``), with an optional due date,
a done mark and a responsible member. Links to a contact/company (crm.contacts) and
a deal (crm.deals) are validated through those features' public services — never by
reading their tables; the assignee through core's public ``TenantDirectory``. When a
linked contact/company/deal is deleted, the bus subscribers (subscribers.py) clear
the link, so the history is kept and nothing dangles.

RBAC is checked here as well as on the route (PermissionGuard). Events are emitted
post-commit by the Service base.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement

from core.auth.access_service import AccessService
from core.auth.guard import PermissionGuard
from core.tenants.directory import TenantDirectory
from modules.crm.activities import permissions as perms
from modules.crm.activities.models import Activity
from modules.crm.activities.repository import ActivityRepository
from modules.crm.activities.schemas import (
    ActivityChanges,
    ActivityDTO,
    ActivityQuery,
    CreateActivityIn,
)
from modules.crm.contacts import ContactsService
from modules.crm.deals import DealService
from shared.context import TenantContext
from shared.errors import InvariantViolationError, NotFoundError
from shared.events import EventBus
from shared.pagination import Page, PageResult
from shared.service import Service, UnitOfWork

# ActivityChanges fields copied onto the activity when set (None = leave unchanged).
_PATCHABLE = (
    "subject",
    "type",
    "notes",
    "due_at",
    "assignee_user_id",
    "contact_id",
    "company_id",
    "deal_id",
)
# Open activities first (by due date, undated last), then the newest.
_ORDER = (
    Activity.done_at.is_not(None),
    Activity.due_at.asc().nulls_last(),
    Activity.created_at.desc(),
)


def _to_dto(activity: Activity) -> ActivityDTO:
    return ActivityDTO.model_validate(activity)


class ActivityService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        contacts: ContactsService,
        deals: DealService,
        types: tuple[str, ...],
        access: AccessService | None = None,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._repo = ActivityRepository(uow.session, ctx)
        self._contacts = contacts
        self._deals = deals
        self._directory = TenantDirectory(uow.session)
        self._types = types
        self._guard = PermissionGuard(access, ctx)

    # --- validation -----------------------------------------------------------

    def _resolve_type(self, activity_type: str | None) -> str:
        if not self._types:
            raise InvariantViolationError("no activity types configured (set CRM_ACTIVITY_TYPES)")
        if activity_type is None:
            return self._types[0]
        if activity_type not in self._types:
            raise InvariantViolationError(f"unknown activity type: {activity_type!r}")
        return activity_type

    async def _validate_links(
        self, contact_id: UUID | None, company_id: UUID | None, deal_id: UUID | None
    ) -> None:
        # Existence in the SAME tenant via the siblings' public interfaces (NotFound
        # for a foreign/missing id) — no cross-feature table read.
        if contact_id is not None:
            await self._contacts.ensure_contact(contact_id)
        if company_id is not None:
            await self._contacts.ensure_company(company_id)
        if deal_id is not None:
            await self._deals.ensure_deal(deal_id)

    async def _require_member(self, user_id: UUID) -> None:
        tenant_id = self.ctx.tenant_id
        if tenant_id is None or not await self._directory.is_active_member(tenant_id, user_id):
            raise NotFoundError(f"assignee {user_id} is not a member of this tenant")

    def _actor_user_id(self) -> UUID | None:
        actor = self.ctx.actor
        return UUID(actor.id) if actor.kind == "user" and actor.id else None

    # --- commands -------------------------------------------------------------

    async def create(self, data: CreateActivityIn) -> ActivityDTO:
        await self._guard.require(perms.ACTIVITY_CREATE)
        activity_type = self._resolve_type(data.type)
        assignee = data.assignee_user_id or self._actor_user_id()
        if assignee is None:
            raise InvariantViolationError("an activity needs an assignee")
        await self._require_member(assignee)
        await self._validate_links(data.contact_id, data.company_id, data.deal_id)
        activity = Activity(
            type=activity_type,
            subject=data.subject,
            notes=data.notes,
            due_at=data.due_at,
            assignee_user_id=assignee,
            contact_id=data.contact_id,
            company_id=data.company_id,
            deal_id=data.deal_id,
        )
        await self._repo.add(activity)
        self.emit("crm.activity.created", {"activity_id": str(activity.id)})
        return _to_dto(activity)

    async def update(self, activity_id: UUID, changes: ActivityChanges) -> ActivityDTO:
        await self._guard.require(perms.ACTIVITY_UPDATE)
        activity = await self._repo.get_or_raise(activity_id)
        if changes.type is not None:
            self._resolve_type(changes.type)
        if changes.assignee_user_id is not None:
            await self._require_member(changes.assignee_user_id)
        await self._validate_links(changes.contact_id, changes.company_id, changes.deal_id)
        previous_assignee = activity.assignee_user_id
        for field in _PATCHABLE:
            value = getattr(changes, field)
            if value is not None:
                setattr(activity, field, value)
            elif field in changes.cleared:
                setattr(activity, field, None)
        await self._session.flush()
        self.emit("crm.activity.updated", {"activity_id": str(activity.id)})
        if activity.assignee_user_id != previous_assignee:
            self.emit(
                "crm.activity.assigned",
                {
                    "activity_id": str(activity.id),
                    "from": str(previous_assignee),
                    "to": str(activity.assignee_user_id),
                },
            )
        return _to_dto(activity)

    async def complete(self, activity_id: UUID) -> ActivityDTO:
        """Mark done (idempotent: a repeat keeps the first time, no event)."""
        await self._guard.require(perms.ACTIVITY_UPDATE)
        activity = await self._repo.get_or_raise(activity_id)
        if activity.done_at is None:
            activity.done_at = datetime.now(UTC)
            await self._session.flush()
            self.emit("crm.activity.completed", {"activity_id": str(activity.id)})
        return _to_dto(activity)

    async def reopen(self, activity_id: UUID) -> ActivityDTO:
        """Mark open again (idempotent: reopening an open activity is a no-op)."""
        await self._guard.require(perms.ACTIVITY_UPDATE)
        activity = await self._repo.get_or_raise(activity_id)
        if activity.done_at is not None:
            activity.done_at = None
            await self._session.flush()
            self.emit("crm.activity.reopened", {"activity_id": str(activity.id)})
        return _to_dto(activity)

    async def delete(self, activity_id: UUID) -> None:
        await self._guard.require(perms.ACTIVITY_DELETE)
        activity = await self._repo.get_or_raise(activity_id)
        await self._repo.delete(activity)
        self.emit("crm.activity.deleted", {"activity_id": str(activity_id)})

    # --- queries --------------------------------------------------------------

    async def get(self, activity_id: UUID) -> ActivityDTO:
        await self._guard.require(perms.ACTIVITY_READ)
        return _to_dto(await self._repo.get_or_raise(activity_id))

    def _filters(self, query: ActivityQuery) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = []
        assignee = query.assignee_user_id
        if query.mine:
            assignee = self._actor_user_id()
            if assignee is None:
                raise InvariantViolationError("'mine' requires a user actor")
        if assignee is not None:
            filters.append(Activity.assignee_user_id == assignee)
        if query.status is not None:
            done = Activity.done_at.is_not(None)
            filters.append(done if query.status == "done" else Activity.done_at.is_(None))
        now = datetime.now(UTC)
        if query.overdue:
            filters += [Activity.done_at.is_(None), Activity.due_at < now]
        if query.due_today:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            filters += [Activity.due_at >= start, Activity.due_at < start + timedelta(days=1)]
        for column, value in (
            (Activity.type, query.type),
            (Activity.deal_id, query.deal_id),
            (Activity.contact_id, query.contact_id),
            (Activity.company_id, query.company_id),
        ):
            if value is not None:
                filters.append(column == value)
        return filters

    async def list(self, page: Page, query: ActivityQuery) -> PageResult[ActivityDTO]:
        await self._guard.require(perms.ACTIVITY_READ)
        result = await self._repo.find_paged(
            *self._filters(query), order_by=list(_ORDER), page=page
        )
        return PageResult(
            items=[_to_dto(a) for a in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
        )

    # --- maintained by the bus subscribers (subscribers.py) --------------------

    async def unlink_contact(self, contact_id: UUID) -> int:
        """Clear a deleted contact from every activity of the tenant."""
        return await self._unlink(Activity.contact_id == contact_id, "contact_id")

    async def unlink_company(self, company_id: UUID) -> int:
        """Clear a deleted company from every activity of the tenant."""
        return await self._unlink(Activity.company_id == company_id, "company_id")

    async def unlink_deal(self, deal_id: UUID) -> int:
        """Clear a deleted deal from every activity of the tenant."""
        return await self._unlink(Activity.deal_id == deal_id, "deal_id")

    async def _unlink(self, where: ColumnElement[bool], attr: str) -> int:
        activities = await self._repo.find(where, for_update=True)
        for activity in activities:
            setattr(activity, attr, None)
        await self._session.flush()
        for activity in activities:
            self.emit("crm.activity.updated", {"activity_id": str(activity.id)})
        return len(activities)
