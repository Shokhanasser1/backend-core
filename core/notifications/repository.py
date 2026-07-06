"""Repositories for the notification tables.

notification_settings is tenant-scoped (standard Repository). notification_outbox
is hybrid (tenant_id nullable = platform send), so it uses a small session-bound
repository that stamps tenant_id from context (NULL for platform sends) and reads
back within the RLS-visible set.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.notifications.models import NotificationOutbox, NotificationSetting
from shared.context import TenantContext
from shared.repository import Repository


class NotificationSettingRepository(Repository[NotificationSetting]):
    model = NotificationSetting

    async def get_by_channel(self, channel: str) -> NotificationSetting | None:
        rows = await self.find(NotificationSetting.channel == channel)
        return rows[0] if rows else None


class NotificationOutboxRepository:
    """Hybrid outbox access. RLS confines app_user to its own tenant; the tenant
    filter here mirrors it explicitly so maintenance (cross-tenant) reads stay
    scoped to the intended tenant/platform slice."""

    def __init__(self, session: AsyncSession, ctx: TenantContext) -> None:
        self._session = session
        self._tenant_id = ctx.tenant_id  # may be None for platform sends

    async def add_all(self, rows: Sequence[NotificationOutbox]) -> None:
        self._session.add_all(list(rows))
        await self._session.flush()

    async def notification_id_for_dedup(self, dedup_key: str) -> UUID | None:
        tenant_pred = (
            NotificationOutbox.tenant_id.is_(None)
            if self._tenant_id is None
            else NotificationOutbox.tenant_id == self._tenant_id
        )
        stmt = select(NotificationOutbox.notification_id).where(
            NotificationOutbox.dedup_key == dedup_key, tenant_pred
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def rows_for_notification(self, notification_id: UUID) -> Sequence[NotificationOutbox]:
        stmt = select(NotificationOutbox).where(
            NotificationOutbox.notification_id == notification_id
        )
        return (await self._session.execute(stmt)).scalars().all()
