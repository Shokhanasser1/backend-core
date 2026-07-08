"""MeteringService — the public interface of saas.metering (§1.2).

A generic usage primitive (owner decision): callers ``record`` usage explicitly at
the domain points that matter; metering does not subscribe to the bus (features may
not use wildcards — §1.1 — and a generic meter must not hardwire other modules'
event names). A caller that meters inside a reliable event handler is
effectively-once for free: the dispatcher's processed_events dedup runs the whole
handler once, so ``record`` runs once too.

Independent of saas.entitlements (owner decision): metering is usage
recording/reporting only; count-limits stay in entitlements via the caller's
current_count. Writes go through the current unit of work (they commit with the
caller's transaction — atomic with the domain fact they measure).
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from modules.saas.metering.models import UsageCounter
from shared.context import TenantContext
from shared.errors import InvariantViolationError
from shared.events import EventBus
from shared.ids import new_uuid7
from shared.service import Service, UnitOfWork

_MAX_METRIC_KEY_LEN = 128


class MeteringService(Service):
    def __init__(self, uow: UnitOfWork, bus: EventBus, ctx: TenantContext) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        if ctx.tenant_id is None:
            raise InvariantViolationError("metering requires a tenant context")
        self._tenant_id = ctx.tenant_id

    async def record(self, metric_key: str, delta: int = 1, *, at: datetime | None = None) -> None:
        """Add ``delta`` to the tenant's counter for ``metric_key`` on the UTC day of
        ``at`` (default now) — an atomic UPSERT, no read-modify-write race. Not
        idempotent by itself: callers that must not double-count meter inside a
        reliable handler (processed_events makes that effectively-once)."""
        if not metric_key or len(metric_key) > _MAX_METRIC_KEY_LEN:
            raise InvariantViolationError("metric_key must be 1..128 chars")
        if delta < 1:
            raise InvariantViolationError("delta must be a positive integer")
        bucket = (at or datetime.now(UTC)).date()
        stmt = pg_insert(UsageCounter).values(
            id=new_uuid7(),
            tenant_id=self._tenant_id,
            metric_key=metric_key,
            bucket=bucket,
            value=delta,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "metric_key", "bucket"],
            set_={"value": UsageCounter.value + delta, "updated_at": func.now()},
        )
        await self._session.execute(stmt)

    async def usage(
        self, metric_key: str, *, since: date | None = None, until: date | None = None
    ) -> int:
        """Total for one metric over an optional [since, until] day window (inclusive)."""
        stmt = select(func.coalesce(func.sum(UsageCounter.value), 0)).where(
            UsageCounter.tenant_id == self._tenant_id, UsageCounter.metric_key == metric_key
        )
        stmt = self._window(stmt, since, until)
        return int((await self._session.execute(stmt)).scalar_one())

    async def summary(
        self, *, since: date | None = None, until: date | None = None
    ) -> dict[str, int]:
        """Per-metric totals for the tenant over an optional day window (for GET /me)."""
        stmt = (
            select(UsageCounter.metric_key, func.sum(UsageCounter.value))
            .where(UsageCounter.tenant_id == self._tenant_id)
            .group_by(UsageCounter.metric_key)
        )
        stmt = self._window(stmt, since, until)
        rows = (await self._session.execute(stmt)).all()
        return {metric_key: int(total) for metric_key, total in rows}

    @staticmethod
    def _window(stmt: Select[Any], since: date | None, until: date | None) -> Select[Any]:
        if since is not None:
            stmt = stmt.where(UsageCounter.bucket >= since)
        if until is not None:
            stmt = stmt.where(UsageCounter.bucket <= until)
        return stmt
