"""commerce_products table (feature commerce.products). Branch commerce_products.

Tenant-scoped catalog with the standard tenant-isolation RLS (enable_tenant_rls):
app_user sees/writes only its own tenant's rows, app_maintenance bypasses for
cross-tenant jobs. FK -> tenants ON DELETE RESTRICT.

Migrations are discovered by folder presence (migrations/discovery.py), so this
runs whenever the feature folder is on disk — independent of ENABLED_MODULES,
which only toggles runtime wiring.

Revision ID: commerce_products0001
Revises: -
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "commerce_products0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("commerce_products",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def upgrade() -> None:
    op.create_table(
        "commerce_products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="UZS"),
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
            name="fk_commerce_products_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_commerce_products_status"),
        sa.CheckConstraint("price_amount >= 0", name="ck_commerce_products_price_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_products"),
    )
    op.create_index(
        "uq_commerce_products_tenant_id_sku",
        "commerce_products",
        ["tenant_id", "sku"],
        unique=True,
    )
    op.create_index(
        "ix_commerce_products_tenant_id_status", "commerce_products", ["tenant_id", "status"]
    )
    enable_tenant_rls("commerce_products")


def downgrade() -> None:
    disable_tenant_rls("commerce_products")
    op.drop_index("ix_commerce_products_tenant_id_status", table_name="commerce_products")
    op.drop_index("uq_commerce_products_tenant_id_sku", table_name="commerce_products")
    op.drop_table("commerce_products")
