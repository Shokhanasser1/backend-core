"""Product ORM model — owns commerce_products (tenant-scoped, RLS).

Internal to the feature: no other feature/module reads this table (ADR-0005).
Money is integer minor units + currency (shared.money convention); no FK to
billing's currencies table (cross-module read is forbidden) — the currency is
validated against the shared CurrencyRegistry at the service boundary.
"""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class Product(TimestampMixin, TenantScopedBase):
    __tablename__ = "commerce_products"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # minor units
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="UZS")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="status"),
        CheckConstraint("price_amount >= 0", name="price_non_negative"),
        Index("uq_commerce_products_tenant_id_sku", "tenant_id", "sku", unique=True),
        Index("ix_commerce_products_tenant_id_status", "tenant_id", "status"),
    )
