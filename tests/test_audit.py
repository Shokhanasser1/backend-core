"""Audit: direct record, append-only enforcement, wildcard sink, dedup
(interfaces §3.5; threat model V10)."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import core.subscribers  # noqa: F401  (register the audit sink on the global bus)
from core.audit.models import AuditLog
from core.audit.service import AuditService
from shared.context import Actor, TenantContext
from shared.events import EventEnvelope, bus
from shared.ids import new_uuid7
from shared.service import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

AUDIT_SINK_ID = "core.audit.subscribers.audit_sink"


async def _count_audit(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()


async def test_direct_record_writes_row(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        await AuditService(uow.session, tenant_ctx).record(
            action="auth.user.password_changed",
            object_type="user",
            object_id=tenant_ctx.actor.id,
            metadata={"reason": "self"},
        )
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        row = (await uow.session.execute(select(AuditLog))).scalar_one()
        assert row.action == "auth.user.password_changed"
        assert row.tenant_id == tenant_ctx.tenant_id
        assert row.payload == {"reason": "self"}


async def test_audit_log_is_append_only(
    session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=tenant_ctx) as uow:
        await AuditService(uow.session, tenant_ctx).record(action="tenants.member.removed")

    # app_user has SELECT + INSERT only — UPDATE and DELETE are denied.
    for sql in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with session_factory() as session:
                from shared.db import apply_tenant_context

                await apply_tenant_context(session, tenant_ctx)
                await session.execute(text(sql))
                await session.commit()


async def test_wildcard_sink_writes_platform_event(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.worker import dispatch_event

    envelope = EventEnvelope(
        event_id=new_uuid7(),
        name="auth.user.registered",
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=None,  # platform event
        actor=Actor(kind="user", id=str(uuid4())),
        payload={"email": "x@example.uz"},
    )
    worker_ctx: dict[str, Any] = {
        "bus": bus,
        "session_factory": session_factory,
        "maintenance_sessions": maintenance_session_factory,
        "job_try": 1,
    }
    await dispatch_event(worker_ctx, AUDIT_SINK_ID, envelope.to_wire())

    async with maintenance_session_factory() as session:
        row = (await session.execute(select(AuditLog))).scalar_one()
        assert row.action == "auth.user.registered"
        assert row.tenant_id is None
        assert row.event_id == envelope.event_id


async def test_sink_dedup_by_event_id(
    maintenance_session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    envelope = EventEnvelope(
        event_id=new_uuid7(),
        name="tenants.tenant.status_changed",
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=tenant_ctx.tenant_id,
        actor=tenant_ctx.actor,
        payload={},
    )
    # record_event is idempotent by event_id (ON CONFLICT DO NOTHING).
    async with SqlAlchemyUnitOfWork(maintenance_session_factory, context=tenant_ctx) as uow:
        service = AuditService(uow.session, tenant_ctx)
        await service.record_event(envelope)
        await service.record_event(envelope)
    assert await _count_audit(maintenance_session_factory) == 1


async def test_excluded_events_not_recorded(
    maintenance_session_factory: async_sessionmaker[AsyncSession], tenant_ctx: TenantContext
) -> None:
    envelope = EventEnvelope(
        event_id=new_uuid7(),
        name="notifications.message.sent",
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=tenant_ctx.tenant_id,
        actor=tenant_ctx.actor,
        payload={},
    )
    async with SqlAlchemyUnitOfWork(maintenance_session_factory, context=tenant_ctx) as uow:
        await AuditService(uow.session, tenant_ctx).record_event(envelope)
    assert await _count_audit(maintenance_session_factory) == 0
