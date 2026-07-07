"""commerce_orders + commerce_order_items (feature commerce.orders). Branch commerce_orders.

Both tenant-scoped with standard RLS. No FK to commerce_products or to billing's
payments (sibling feature / other module — validated/linked by value, §1.1).

Revision ID: commerce_orders0001
Revises: -
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "commerce_orders0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("commerce_orders",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def upgrade() -> None:
    op.create_table(
        "commerce_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("customer_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("total_amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="UZS"),
        sa.Column("payment_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_commerce_orders_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'canceled')", name="ck_commerce_orders_status"
        ),
        sa.CheckConstraint("total_amount >= 0", name="ck_commerce_orders_total_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_orders"),
    )
    op.create_index(
        "ix_commerce_orders_tenant_id_customer_user_id",
        "commerce_orders",
        ["tenant_id", "customer_user_id"],
    )
    op.create_index(
        "ix_commerce_orders_tenant_id_status", "commerce_orders", ["tenant_id", "status"]
    )
    op.create_table(
        "commerce_order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.SmallInteger(), nullable=False),
        sa.Column("unit_price_amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="UZS"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_commerce_order_items_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["commerce_orders.id"],
            name="fk_commerce_order_items_order_id_commerce_orders",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("quantity >= 1", name="ck_commerce_order_items_quantity"),
        sa.CheckConstraint(
            "unit_price_amount >= 0", name="ck_commerce_order_items_price_non_negative"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_order_items"),
    )
    op.create_index("ix_commerce_order_items_order_id", "commerce_order_items", ["order_id"])
    enable_tenant_rls("commerce_orders")
    enable_tenant_rls("commerce_order_items")


def downgrade() -> None:
    disable_tenant_rls("commerce_order_items")
    disable_tenant_rls("commerce_orders")
    op.drop_index("ix_commerce_order_items_order_id", table_name="commerce_order_items")
    op.drop_table("commerce_order_items")
    op.drop_index("ix_commerce_orders_tenant_id_status", table_name="commerce_orders")
    op.drop_index("ix_commerce_orders_tenant_id_customer_user_id", table_name="commerce_orders")
    op.drop_table("commerce_orders")
