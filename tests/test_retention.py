"""Retention sweeps: audit_log (as app_retention), processed_events and the
notification outbox's terminal PII rows (as app_maintenance). Schema §2.4/§2.5/§2.7.

The audit case doubles as the proof that app_retention — the only role with DELETE
on audit_log — actually has a working RLS policy (added in core_audit0002);
without it the delete would silently affect zero rows.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.worker import purge_retention
from core.audit.models import AuditLog
from core.audit.retention import purge_expired_audit
from core.notifications.models import NotificationOutbox
from core.notifications.retention import purge_expired_notifications
from shared.context import TenantContext
from shared.ids import new_uuid7
from shared.processed_events import ProcessedEvent, purge_processed_events
from shared.service import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

_SETTINGS = Settings(
    _env_file=None,
    audit_retention_days=1,
    processed_events_retention_days=1,
    notification_retention_days=1,
)

_OLD = datetime.now(UTC) - timedelta(days=10)
_RECENT = datetime.now(UTC) - timedelta(hours=1)


async def _count(factory: async_sessionmaker[AsyncSession], model: type) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_purge_audit_deletes_old_keeps_recent(
    session_factory: async_sessionmaker[AsyncSession],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    retention_session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        for created_at, action in ((_OLD, "old"), (_RECENT, "new")):
            uow.session.add(
                AuditLog(
                    id=new_uuid7(),
                    tenant_id=tenant_ctx.tenant_id,
                    action=action,
                    payload={},
                    created_at=created_at,
                )
            )
        await uow.session.flush()

    deleted = await purge_expired_audit(retention_session_factory, _SETTINGS)

    assert deleted == 1
    async with maintenance_session_factory() as session:
        rows = (await session.execute(select(AuditLog.action))).scalars().all()
    assert list(rows) == ["new"]


async def test_purge_audit_no_op_when_all_recent(
    session_factory: async_sessionmaker[AsyncSession],
    retention_session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        uow.session.add(
            AuditLog(
                id=new_uuid7(),
                tenant_id=tenant_ctx.tenant_id,
                action="fresh",
                payload={},
                created_at=_RECENT,
            )
        )
        await uow.session.flush()
    assert await purge_expired_audit(retention_session_factory, _SETTINGS) == 0


async def test_purge_processed_events(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with maintenance_session_factory() as session:
        session.add(ProcessedEvent(handler="h", event_id=uuid4(), processed_at=_OLD))
        session.add(ProcessedEvent(handler="h", event_id=uuid4(), processed_at=_RECENT))
        await session.commit()

    deleted = await purge_processed_events(maintenance_session_factory, _SETTINGS)
    assert deleted == 1
    assert await _count(maintenance_session_factory, ProcessedEvent) == 1


def _outbox_row(status: str, created_at: datetime) -> NotificationOutbox:
    return NotificationOutbox(
        id=new_uuid7(),
        notification_id=uuid4(),
        tenant_id=None,  # platform row — no tenant FK to satisfy
        channel="email",
        recipient="user@example.uz",
        template_key="billing.payment_succeeded",
        status=status,
        created_at=created_at,
    )


async def test_purge_notifications_only_terminal_and_old(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with maintenance_session_factory() as session:
        session.add(_outbox_row("sent", _OLD))  # terminal + old -> deleted
        session.add(_outbox_row("dead", _OLD))  # terminal + old -> deleted
        session.add(_outbox_row("pending", _OLD))  # non-terminal -> kept (still in flight)
        session.add(_outbox_row("sent", _RECENT))  # terminal but recent -> kept
        await session.commit()

    deleted = await purge_expired_notifications(maintenance_session_factory, _SETTINGS)
    assert deleted == 2

    async with maintenance_session_factory() as session:
        remaining = (await session.execute(select(NotificationOutbox.status))).scalars().all()
    assert sorted(remaining) == ["pending", "sent"]


async def test_purge_retention_job_runs_every_sweep(
    session_factory: async_sessionmaker[AsyncSession],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    retention_session_factory: async_sessionmaker[AsyncSession],
    tenant_ctx: TenantContext,
) -> None:
    """The worker's daily job drains all three tables and reports per-table counts."""
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        uow.session.add(
            AuditLog(
                id=new_uuid7(),
                tenant_id=tenant_ctx.tenant_id,
                action="old",
                payload={},
                created_at=_OLD,
            )
        )
        await uow.session.flush()
    async with maintenance_session_factory() as session:
        session.add(ProcessedEvent(handler="h", event_id=uuid4(), processed_at=_OLD))
        session.add(_outbox_row("sent", _OLD))
        await session.commit()

    ctx: dict[str, Any] = {
        "settings": _SETTINGS,
        "retention_sessions": retention_session_factory,
        "maintenance_sessions": maintenance_session_factory,
    }
    counts = await purge_retention(ctx)
    assert counts == {"audit_log": 1, "processed_events": 1, "notification_outbox": 1}
