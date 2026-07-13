"""Deal ORM model — owns crm_deals (tenant-scoped, RLS).

A sales opportunity moving through a configurable funnel. Money is integer minor
units + currency (shared.money convention). ``contact_id``/``company_id`` are bare
Uuids (no cross-feature FK — crm.contacts is a separate, independently transferable
feature); they are validated through ContactsService at the service boundary, the
same way commerce.cart validates products through ProductService. Internal to the
feature: no one reads this table — siblings call DealService.
"""

import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class Deal(TimestampMixin, TenantScopedBase):
    __tablename__ = "crm_deals"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)  # one of CRM_DEAL_STAGES
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # minor units
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="UZS")
    # Optional links to crm.contacts entities (same tenant), validated via
    # ContactsService — bare Uuids, never a cross-feature FK.
    contact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    company_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_crm_deals_tenant_id_stage", "tenant_id", "stage"),
        Index("ix_crm_deals_tenant_id_contact_id", "tenant_id", "contact_id"),
    )
