"""Notification outbox retention sweep (schema §2.4).

``recipient`` is personal data (email / phone / chat id) and must not be kept
indefinitely (UZ personal-data law, threat model). Terminal rows (``sent`` /
``dead``) older than the retention horizon are deleted; the durable "a
notification was sent" fact lives on in ``audit_log`` without the recipient.
Non-terminal rows are never touched — they are still in flight.

Runs as app_maintenance (the role granted DELETE on the outbox). Bounded per run
so a large backlog drains over several runs, not one long transaction.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.notifications.models import NotificationOutbox
from shared.config import Settings

logger = logging.getLogger(__name__)

_SWEEP_BATCH = 1000
_TERMINAL_STATES = ("sent", "dead")


async def purge_expired_notifications(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> int:
    """Delete a bounded batch of terminal outbox rows past the retention horizon.
    Returns the number deleted."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.notification_retention_days)
    async with maintenance_sessions() as session:
        oldest = (
            select(NotificationOutbox.id)
            .where(
                NotificationOutbox.status.in_(_TERMINAL_STATES),
                NotificationOutbox.created_at < cutoff,
            )
            .order_by(NotificationOutbox.created_at)
            .limit(_SWEEP_BATCH)
            .scalar_subquery()
        )
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                delete(NotificationOutbox).where(NotificationOutbox.id.in_(oldest))
            ),
        )
        await session.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info("purged expired notifications", extra={"count": deleted})
    return deleted
