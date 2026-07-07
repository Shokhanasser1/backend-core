"""Order ORM models — owns commerce_orders, commerce_order_items (tenant-scoped, RLS).

``customer_user_id`` and ``product_id`` carry no FK (global users / sibling
feature). ``payment_id`` is billing's id stored as an opaque reference — no FK
across the module boundary (ADR-0005); the order↔payment link is by value only.
"""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, SmallInteger, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class Order(TimestampMixin, TenantScopedBase):
    __tablename__ = "commerce_orders"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # no FK (global)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    total_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # minor units
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="UZS")
    payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # billing id by value, no FK

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'paid', 'canceled')", name="ck_commerce_orders_status"
        ),
        CheckConstraint("total_amount >= 0", name="ck_commerce_orders_total_non_negative"),
        Index("ix_commerce_orders_tenant_id_customer_user_id", "tenant_id", "customer_user_id"),
        Index("ix_commerce_orders_tenant_id_status", "tenant_id", "status"),
    )


class OrderItem(TimestampMixin, TenantScopedBase):
    __tablename__ = "commerce_order_items"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("commerce_orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # no FK (sibling feature)
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    unit_price_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # snapshot
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="UZS")

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="ck_commerce_order_items_quantity"),
        CheckConstraint(
            "unit_price_amount >= 0", name="ck_commerce_order_items_price_non_negative"
        ),
        Index("ix_commerce_order_items_order_id", "order_id"),
    )
