"""Notifications ORM models (schema §2.4).

- notification_settings — tenant-scoped per-channel config (encrypted secrets).
- notification_outbox — hybrid (tenant_id NULL = platform send: email
  verification, password reset); Postgres-backed delivery queue with a
  ``SELECT ... FOR UPDATE SKIP LOCKED`` + lease dispatch contract.
These are internals of core/notifications: no other module reads them (ADR-0005).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    LargeBinary,
    SmallInteger,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, TenantScopedBase, TimestampMixin
from shared.ids import new_uuid7


class NotificationSetting(TimestampMixin, TenantScopedBase):
    __tablename__ = "notification_settings"

    # 'telegram' | 'sms_eskiz' | 'email'; no CHECK — channels are extensible.
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    # App-level encrypted JSON config (Fernet/MultiFernet, key from env — OV-19);
    # write-only contract (threat model V10): tokens/keys never leave the DB.
    config_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_notification_settings_tenant_id_channel",
            "tenant_id",
            "channel",
            unique=True,
        ),
    )


class NotificationOutbox(TimestampMixin, Base):
    __tablename__ = "notification_outbox"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    # Groups the rows of one send() call (one row per channel); returned by send.
    notification_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    dedup_key: Mapped[str | None] = mapped_column(Text)  # NULL = no dedup
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # FK added in migration
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    recipient: Mapped[str] = mapped_column(Text, nullable=False)  # chat_id | E.164 | email
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = mapped_column(Text, nullable=False, default="ru")
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    # Next attempt (backoff); for status='sending' this is the lease deadline.
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','sending','sent','failed','dead')",
            name="status",
        ),
        # Idempotency of send(); NULLS NOT DISTINCT so platform sends
        # (tenant_id NULL) also dedup. channel in the key: one send writes a row
        # per channel. Added in the migration (NULLS NOT DISTINCT needs raw DDL).
        Index("ix_notification_outbox_notification_id", "notification_id"),
        Index("ix_notification_outbox_tenant_id_created_at", "tenant_id", "created_at"),
    )
