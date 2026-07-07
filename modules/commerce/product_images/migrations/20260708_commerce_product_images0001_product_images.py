"""commerce_product_images table (feature commerce.product_images). Branch
commerce_product_images.

Tenant-scoped links between a product and a stored file, with the standard
tenant-isolation RLS. ``product_id`` (commerce.products) and ``file_id``
(core/files) carry NO cross-table FK — those tables belong to other components;
integrity is enforced through their public services at write time.

Migrations are discovered by folder presence (migrations/discovery.py), so this
runs whenever the feature folder is on disk — independent of ENABLED_MODULES.

Revision ID: commerce_product_images0001
Revises: -
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "commerce_product_images0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("commerce_product_images",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def upgrade() -> None:
    op.create_table(
        "commerce_product_images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),  # no FK (sibling feature)
        sa.Column("file_id", sa.Uuid(), nullable=False),  # no FK (core/files)
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alt_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_commerce_product_images_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_product_images"),
    )
    op.create_index(
        "ix_commerce_product_images_tenant_id_product_id",
        "commerce_product_images",
        ["tenant_id", "product_id"],
    )
    enable_tenant_rls("commerce_product_images")


def downgrade() -> None:
    disable_tenant_rls("commerce_product_images")
    op.drop_index(
        "ix_commerce_product_images_tenant_id_product_id",
        table_name="commerce_product_images",
    )
    op.drop_table("commerce_product_images")
