"""Audit retention sweep (schema §2.5, decision OV-27).

Deletes ``audit_log`` rows older than the retention horizon (default 24 months,
env-overridable). Runs as ``app_retention`` — the ONLY role granted DELETE on
``audit_log`` — through its own engine, so a compromise of the app or the
maintenance paths cannot erase the journal (defense in depth, threat model V10).

Each run is bounded so a large backlog drains over several runs rather than one
long-held transaction. The delete targets the primary key of an ordered,
LIMITed subquery.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.audit.models import AuditLog
from shared.config import Settings

logger = logging.getLogger(__name__)

# Bound each sweep so a large backlog is drained over several runs, not one long tx.
_SWEEP_BATCH = 1000


async def purge_expired_audit(
    retention_sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> int:
    """Delete a bounded batch of audit rows past the retention horizon. Returns the
    number deleted; callers loop the cron, not this function."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.audit_retention_days)

    async with retention_sessions() as session:
        oldest = (
            select(AuditLog.id)
            .where(AuditLog.created_at < cutoff)
            .order_by(AuditLog.created_at)
            .limit(_SWEEP_BATCH)
            .scalar_subquery()
        )
        result = cast(
            "CursorResult[Any]",
            await session.execute(delete(AuditLog).where(AuditLog.id.in_(oldest))),
        )
        await session.commit()

    deleted = result.rowcount or 0
    if deleted:
        logger.info("purged expired audit rows", extra={"count": deleted})
    return deleted
