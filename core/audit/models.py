"""Audit log model (schema §2.5).

Append-only and hybrid: tenant_id/user_id are nullable (system events, anonymous
failed logins) and carry NO foreign keys — the journal must survive deletion of
any entity. There is deliberately no updated_at (the row never changes) and no
update/delete methods; the append-only guarantee is enforced by DB grants.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base
from shared.ids import new_uuid7


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # no FK — historical
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # NULL = system/anonymous
    request_id: Mapped[str | None] = mapped_column(Text)
    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # dedup with bus sink
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str | None] = mapped_column(Text)
    object_id: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # one action = one row: dedup between the direct record and the bus sink.
        Index(
            "uq_audit_log_event_id",
            "event_id",
            unique=True,
            postgresql_where=text("event_id IS NOT NULL"),
        ),
        Index("ix_audit_log_tenant_id_created_at", "tenant_id", created_at.desc()),
        Index(
            "ix_audit_log_tenant_object", "tenant_id", "object_type", "object_id", created_at.desc()
        ),
        Index("ix_audit_log_tenant_user", "tenant_id", "user_id", created_at.desc()),
        Index("ix_audit_log_created_at_brin", "created_at", postgresql_using="brin"),
    )
