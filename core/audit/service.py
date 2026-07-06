"""AuditService — append-only writes (interfaces doc §3.5, decision OV-26).

Two write paths, deduplicated by event_id:
- ``record`` — a critical action writes directly inside its business
  transaction, passing the event_id of the event emitted in the same
  transaction (one action = one row);
- ``record_event`` — the wildcard bus sink writes idempotently from the
  envelope (``ON CONFLICT DO NOTHING`` on the partial unique index).

No update/delete methods exist — append-only is the shape of the interface,
backed by DB grants.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.models import AuditLog
from shared.context import RequestContext, TenantContext
from shared.events import EventEnvelope
from shared.ids import new_uuid7

# High-frequency telemetry we do not mirror into the audit journal (§3.5).
AUDIT_EXCLUDED_EVENTS = frozenset({"notifications.message.sent"})

_DEDUP_KW: dict[str, Any] = {
    "index_elements": ["event_id"],
    "index_where": text("event_id IS NOT NULL"),
}


def _actor_user_id(ctx: TenantContext) -> UUID | None:
    if ctx.actor.kind == "user" and ctx.actor.id:
        return UUID(ctx.actor.id)
    return None


class AuditService:
    def __init__(self, session: AsyncSession, ctx: TenantContext) -> None:
        self._session = session
        self._ctx = ctx

    async def record(
        self,
        *,
        action: str,
        object_type: str | None = None,
        object_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        event_id: UUID | None = None,
        request: RequestContext | None = None,
    ) -> None:
        """Write an audit row in the current transaction (tenant_id/actor from
        ctx). Idempotent by event_id when one is supplied."""
        stmt = pg_insert(AuditLog).values(
            id=new_uuid7(),
            tenant_id=self._ctx.tenant_id,
            user_id=_actor_user_id(self._ctx),
            request_id=self._ctx.request_id,
            event_id=event_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            ip=request.ip if request else None,
            user_agent=request.user_agent if request else None,
            payload=dict(metadata or {}),
        )
        if event_id is not None:
            stmt = stmt.on_conflict_do_nothing(**_DEDUP_KW)
        await self._session.execute(stmt)

    async def record_event(self, event: EventEnvelope) -> None:
        """Wildcard-sink path: mirror a bus event into the journal, skipping the
        exclusion list, idempotent by event_id."""
        if event.name in AUDIT_EXCLUDED_EVENTS:
            return
        user_id = UUID(event.actor.id) if event.actor.kind == "user" and event.actor.id else None
        stmt = (
            pg_insert(AuditLog)
            .values(
                id=new_uuid7(),
                tenant_id=event.tenant_id,
                user_id=user_id,
                event_id=event.event_id,
                action=event.name,
                payload=dict(event.payload),
            )
            .on_conflict_do_nothing(**_DEDUP_KW)
        )
        await self._session.execute(stmt)
