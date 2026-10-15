"""crm_activities table (feature crm.activities). Branch crm_activities.

Tenant-scoped with the standard tenant-isolation RLS (enable_tenant_rls): app_user
reads/writes only its own tenant's rows, app_maintenance bypasses for cross-tenant
jobs. FK -> tenants ON DELETE RESTRICT. assignee_user_id has no FK (users are
global); contact_id/company_id/deal_id are bare Uuids (no cross-feature FK) —
validated through the siblings' public services at the service boundary.

Migrations are discovered by folder presence (migrations/discovery.py), so this
runs whenever the feature folder is on disk — independent of ENABLED_MODULES.

Revision ID: crm_activities0001
Revises: -
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "crm_activities0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("crm_activities",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")

_INDEXES = (
    (
        "ix_crm_activities_tenant_id_assignee_user_id_done_at",
        ["tenant_id", "assignee_user_id", "done_at"],
    ),
    ("ix_crm_activities_tenant_id_due_at", ["tenant_id", "due_at"]),
    ("ix_crm_activities_tenant_id_deal_id", ["tenant_id", "deal_id"]),
    ("ix_crm_activities_tenant_id_contact_id", ["tenant_id", "contact_id"]),
)


def upgrade() -> None:
    op.create_table(
        "crm_activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assignee_user_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("deal_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_crm_activities_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_crm_activities"),
    )
    for name, columns in _INDEXES:
        op.create_index(name, "crm_activities", columns)
    enable_tenant_rls("crm_activities")


def downgrade() -> None:
    disable_tenant_rls("crm_activities")
    for name, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name="crm_activities")
    op.drop_table("crm_activities")
