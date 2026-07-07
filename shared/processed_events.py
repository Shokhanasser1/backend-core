"""Event-bus deduplication table (schema doc §2.7).

Global service table — a sanctioned exception from tenant RLS: contains no
business data, event_id is globally unique, and platform events (tenant_id
is None in the envelope) must deduplicate the same way. Rows are written and
read only by the bus dispatcher; the retention sweep (Phase 4) is below.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import DateTime, Index, Text, Uuid, delete, func
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from shared.config import Settings
from shared.db import GlobalBase

logger = logging.getLogger(__name__)


class ProcessedEvent(GlobalBase):
    __tablename__ = "processed_events"

    # Composite PK (handler, event_id) IS the deduplication mechanism.
    handler: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # Immutable rows: no created_at/updated_at convention here (schema §1.3 exception).
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_processed_events_processed_at", "processed_at"),)


async def purge_processed_events(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> int:
    """Delete dedup keys older than the retention horizon (schema §2.7).

    They matter only over the bus's retry horizon (a handful of tries with
    backoff); beyond that they are dead weight. Runs as app_maintenance (the role
    granted DELETE here); returns the number deleted."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.processed_events_retention_days)
    async with maintenance_sessions() as session:
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                delete(ProcessedEvent).where(ProcessedEvent.processed_at < cutoff)
            ),
        )
        await session.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info("purged processed_events", extra={"count": deleted})
    return deleted
