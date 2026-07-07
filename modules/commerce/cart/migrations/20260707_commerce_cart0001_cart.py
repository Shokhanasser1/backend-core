"""commerce_carts + commerce_cart_items (feature commerce.cart). Branch commerce_cart.

Both tenant-scoped with standard RLS. No FK to commerce_products (sibling feature —
product_id is validated through ProductService, not joined). customer_user_id has
no FK (users are global, core/auth-owned).

Revision ID: commerce_cart0001
Revises: -
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "commerce_cart0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("commerce_cart",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def upgrade() -> None:
    op.create_table(
        "commerce_carts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("customer_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_commerce_carts_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("status IN ('active', 'checked_out')", name="ck_commerce_carts_status"),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_carts"),
    )
    op.create_index(
        "ix_commerce_carts_tenant_id_customer_user_id",
        "commerce_carts",
        ["tenant_id", "customer_user_id"],
    )
    op.create_table(
        "commerce_cart_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=False),
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
            name="fk_commerce_cart_items_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"],
            ["commerce_carts.id"],
            name="fk_commerce_cart_items_cart_id_commerce_carts",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("quantity >= 1", name="ck_commerce_cart_items_quantity"),
        sa.CheckConstraint(
            "unit_price_amount >= 0", name="ck_commerce_cart_items_price_non_negative"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_cart_items"),
    )
    op.create_index(
        "uq_commerce_cart_items_cart_id_product_id",
        "commerce_cart_items",
        ["cart_id", "product_id"],
        unique=True,
    )
    enable_tenant_rls("commerce_carts")
    enable_tenant_rls("commerce_cart_items")


def downgrade() -> None:
    disable_tenant_rls("commerce_cart_items")
    disable_tenant_rls("commerce_carts")
    op.drop_index("uq_commerce_cart_items_cart_id_product_id", table_name="commerce_cart_items")
    op.drop_table("commerce_cart_items")
    op.drop_index("ix_commerce_carts_tenant_id_customer_user_id", table_name="commerce_carts")
    op.drop_table("commerce_carts")
