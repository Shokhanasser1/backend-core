"""Usage-counter retention sweep.

Deletes ``saas_usage_counters`` buckets older than the retention horizon (default
~13 months, env-overridable via SAAS_USAGE_RETENTION_DAYS). Runs as
``app_maintenance`` (cross-tenant, bypasses RLS via the maintenance_all policy) —
usage counters are operational data, not the audit journal, so they follow the
same maintenance-retention pattern as processed_events.

Each run is bounded so a large backlog drains over several runs rather than one
long-held transaction (the worker's purge_retention cron loops it). Wired into
the worker only when the saas module is enabled (app/worker.py).
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.saas.metering.models import UsageCounter
from shared.config import Settings

logger = logging.getLogger(__name__)

# Bound each sweep so a large backlog is drained over several runs, not one long tx.
_SWEEP_BATCH = 1000


async def purge_expired_usage(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> int:
    """Delete a bounded batch of usage buckets past the retention horizon. Returns
    the number deleted; callers loop the cron, not this function."""
    cutoff = (datetime.now(UTC) - timedelta(days=settings.saas_usage_retention_days)).date()

    async with maintenance_sessions() as session:
        oldest = (
            select(UsageCounter.id)
            .where(UsageCounter.bucket < cutoff)
            .order_by(UsageCounter.bucket)
            .limit(_SWEEP_BATCH)
            .scalar_subquery()
        )
        result = cast(
            "CursorResult[Any]",
            await session.execute(delete(UsageCounter).where(UsageCounter.id.in_(oldest))),
        )
        await session.commit()

    deleted = result.rowcount or 0
    if deleted:
        logger.info("purged expired usage counters", extra={"count": deleted})
    return deleted
