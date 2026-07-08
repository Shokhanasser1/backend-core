"""EntitlementService — the public interface of saas.entitlements (§1.2).

Sibling features enforce their tariff limits through this service without ever
reading the entitlement tables: e.g. ``commerce.products`` would call
``require_within_limit("commerce.product", current_count)`` before creating a
product. The subscribers (subscribers.py) keep the tenant's active plan in step
with billing; this service resolves it against the plan grid.

Default policy (documented, deliberate — this is a reusable template):

- No active plan (no subscription, or a canceled one past its period end) is the
  "unconfigured" state → every flag reads disabled and every limit unlimited.
- A flag not present in the grid reads disabled; a limit not present reads
  unlimited (None). Enforcement bites only where a limit is explicitly seeded, so
  enabling this feature never silently locks a tenant out of the whole app. A
  client that wants a hard floor gives every tenant the billing free plan
  (auto-subscribe) with explicit limits.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from modules.saas.entitlements.models import PlanEntitlement, TenantEntitlement
from modules.saas.entitlements.repository import TenantEntitlementRepository
from modules.saas.entitlements.schemas import EntitlementsDTO
from shared.context import TenantContext
from shared.errors import ConflictError
from shared.events import EventBus
from shared.service import Service, UnitOfWork


class EntitlementService(Service):
    def __init__(self, uow: UnitOfWork, bus: EventBus, ctx: TenantContext) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._repo = TenantEntitlementRepository(uow.session, ctx)

    # --- public: reads for sibling features and the /me route ---

    async def effective_plan_code(self) -> str | None:
        """The tenant's active plan_code, or None if none is in force.

        A canceled subscription still covers the tenant until its period end
        (cancel-at-period-end semantics); only afterwards does coverage lapse.
        """
        row = await self._current_row()
        if row is None:
            return None
        if (
            row.canceled
            and row.current_period_end is not None
            and datetime.now(UTC) >= row.current_period_end
        ):
            return None
        return row.plan_code

    async def is_enabled(self, flag_key: str) -> bool:
        """Whether the active plan grants the boolean feature flag (default False)."""
        plan = await self.effective_plan_code()
        if plan is None:
            return False
        row = await self._grant(plan, flag_key, "flag")
        return bool(row.bool_value) if row is not None else False

    async def get_limit(self, limit_key: str) -> int | None:
        """The active plan's numeric limit, or None for unlimited/unset."""
        plan = await self.effective_plan_code()
        if plan is None:
            return None
        row = await self._grant(plan, limit_key, "limit")
        return row.int_value if row is not None else None

    async def require_within_limit(self, limit_key: str, current_count: int) -> None:
        """Guard for sibling features: raise ConflictError (409) if creating one
        more would exceed the plan's limit. ``current_count`` is the count BEFORE
        the new object. No-op when the limit is unlimited/unset."""
        limit = await self.get_limit(limit_key)
        if limit is not None and current_count >= limit:
            raise ConflictError(f"plan limit '{limit_key}' reached ({current_count}/{limit})")

    async def snapshot(self) -> EntitlementsDTO:
        """All flags + limits of the active plan (for GET /me / a UI wizard)."""
        plan = await self.effective_plan_code()
        flags: dict[str, bool] = {}
        limits: dict[str, int | None] = {}
        if plan is not None:
            rows = (
                await self._session.execute(
                    select(PlanEntitlement).where(PlanEntitlement.plan_code == plan)
                )
            ).scalars()
            for row in rows:
                if row.kind == "flag":
                    flags[row.entitlement_key] = bool(row.bool_value)
                else:
                    limits[row.entitlement_key] = row.int_value
        return EntitlementsDTO(plan_code=plan, flags=flags, limits=limits)

    # --- maintained by the bus subscribers (subscribers.py) ---

    async def set_active_plan(self, plan_code: str, current_period_end: datetime | None) -> None:
        """Upsert the tenant's active plan (billing.subscription.activated)."""
        row = await self._current_row()
        if row is None:
            await self._repo.add(
                TenantEntitlement(
                    plan_code=plan_code,
                    current_period_end=current_period_end,
                    canceled=False,
                )
            )
        else:
            row.plan_code = plan_code
            row.current_period_end = current_period_end
            row.canceled = False
            await self._session.flush()
        self.emit("saas.entitlement.changed", {"plan_code": plan_code})

    async def mark_canceled(self) -> None:
        """Flag the active plan as cancel-at-period-end (billing.subscription.canceled).
        Coverage still holds until the period end (see effective_plan_code)."""
        row = await self._current_row()
        if row is None:
            return  # nothing active to cancel — idempotent
        row.canceled = True
        await self._session.flush()
        self.emit("saas.entitlement.changed", {"plan_code": row.plan_code})

    # --- internals ---

    async def _current_row(self) -> TenantEntitlement | None:
        rows = await self._repo.find(page=None)  # at most one (unique per tenant)
        return rows[0] if rows else None

    async def _grant(self, plan_code: str, key: str, kind: str) -> PlanEntitlement | None:
        return (
            await self._session.execute(
                select(PlanEntitlement).where(
                    PlanEntitlement.plan_code == plan_code,
                    PlanEntitlement.entitlement_key == key,
                    PlanEntitlement.kind == kind,
                )
            )
        ).scalar_one_or_none()
