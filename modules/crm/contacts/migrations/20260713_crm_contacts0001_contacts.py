"""crm_companies + crm_contacts tables (feature crm.contacts). Branch crm_contacts.

Two tenant-scoped tables with the standard tenant-isolation RLS
(enable_tenant_rls): app_user reads/writes only its own tenant's rows,
app_maintenance bypasses for cross-tenant jobs. Both FK -> tenants ON DELETE
RESTRICT. crm_contacts.company_id is an intra-feature FK -> crm_companies with
ON DELETE SET NULL (deleting a company un-assigns its contacts).

Migrations are discovered by folder presence (migrations/discovery.py), so this
runs whenever the feature folder is on disk — independent of ENABLED_MODULES,
which only toggles runtime wiring.

Revision ID: crm_contacts0001
Revises: -
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "crm_contacts0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("crm_contacts",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def upgrade() -> None:
    op.create_table(
        "crm_companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_crm_companies_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_crm_companies"),
    )
    op.create_index("ix_crm_companies_tenant_id_name", "crm_companies", ["tenant_id", "name"])
    enable_tenant_rls("crm_companies")

    op.create_table(
        "crm_contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("position", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_crm_contacts_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["crm_companies.id"],
            name="fk_crm_contacts_company_id_crm_companies",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_crm_contacts"),
    )
    op.create_index(
        "ix_crm_contacts_tenant_id_company_id", "crm_contacts", ["tenant_id", "company_id"]
    )
    enable_tenant_rls("crm_contacts")


def downgrade() -> None:
    disable_tenant_rls("crm_contacts")
    op.drop_index("ix_crm_contacts_tenant_id_company_id", table_name="crm_contacts")
    op.drop_table("crm_contacts")

    disable_tenant_rls("crm_companies")
    op.drop_index("ix_crm_companies_tenant_id_name", table_name="crm_companies")
    op.drop_table("crm_companies")
