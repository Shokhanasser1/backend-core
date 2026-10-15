"""crm.activities ORM model — owns crm_activities (tenant-scoped, RLS).

One entity for both the plan and the history of touches (Pipedrive-style): a call,
a meeting, a task or an email, with an optional due date and a done mark.
``assignee_user_id`` has no FK (users are global, owned by core/auth); the links to
crm.contacts / crm.deals entities are bare Uuids validated through their public
services, never a cross-feature FK.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class Activity(TimestampMixin, TenantScopedBase):
    __tablename__ = "crm_activities"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)  # one of CRM_ACTIVITY_TYPES
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = open
    assignee_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # no FK (global)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    company_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        Index(
            "ix_crm_activities_tenant_id_assignee_user_id_done_at",
            "tenant_id",
            "assignee_user_id",
            "done_at",
        ),
        Index("ix_crm_activities_tenant_id_due_at", "tenant_id", "due_at"),
        Index("ix_crm_activities_tenant_id_deal_id", "tenant_id", "deal_id"),
        Index("ix_crm_activities_tenant_id_contact_id", "tenant_id", "contact_id"),
    )
