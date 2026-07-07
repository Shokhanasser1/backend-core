"""Cart ORM models — owns commerce_carts, commerce_cart_items (tenant-scoped, RLS).

``customer_user_id`` carries no FK (users are global, owned by core/auth — no
cross-module FK, ADR-0005). ``product_id`` carries no FK either: products belong
to a sibling feature and are validated through ProductService, never joined
(interfaces §1.1). Only cart_id -> carts is a real FK (same feature).
"""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, SmallInteger, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class Cart(TimestampMixin, TenantScopedBase):
    __tablename__ = "commerce_carts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # no FK (global)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'checked_out')", name="ck_commerce_carts_status"),
        Index("ix_commerce_carts_tenant_id_customer_user_id", "tenant_id", "customer_user_id"),
    )


class CartItem(TimestampMixin, TenantScopedBase):
    __tablename__ = "commerce_cart_items"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    cart_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("commerce_carts.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # no FK (sibling feature)
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    unit_price_amount: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )  # snapshot, minor units
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="UZS")

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="ck_commerce_cart_items_quantity"),
        CheckConstraint("unit_price_amount >= 0", name="ck_commerce_cart_items_price_non_negative"),
        Index("uq_commerce_cart_items_cart_id_product_id", "cart_id", "product_id", unique=True),
    )
