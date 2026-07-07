"""files table (core module files). Branch core_files.

Tenant-scoped metadata for stored objects with the standard tenant-isolation RLS
(enable_tenant_rls): app_user sees/writes only its own tenant's rows,
app_maintenance bypasses for cross-tenant jobs. FK -> tenants ON DELETE RESTRICT.
The bytes live in the storage backend under storage_key; this row is metadata +
integrity checksum only.

Revision ID: core_files0001
Revises: -
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "core_files0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("core_files",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_files_tenant_id_tenants", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("byte_size >= 0", name="ck_files_byte_size_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_files"),
    )
    op.create_index("ix_files_tenant_id", "files", ["tenant_id"])
    op.create_index(
        "uq_files_tenant_id_storage_key", "files", ["tenant_id", "storage_key"], unique=True
    )
    enable_tenant_rls("files")


def downgrade() -> None:
    disable_tenant_rls("files")
    op.drop_index("uq_files_tenant_id_storage_key", table_name="files")
    op.drop_index("ix_files_tenant_id", table_name="files")
    op.drop_table("files")
