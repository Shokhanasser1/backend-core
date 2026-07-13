"""crm_deals table (feature crm.deals). Branch crm_deals.

Tenant-scoped pipeline with the standard tenant-isolation RLS (enable_tenant_rls):
app_user reads/writes only its own tenant's rows, app_maintenance bypasses for
cross-tenant jobs. FK -> tenants ON DELETE RESTRICT. contact_id/company_id are bare
Uuids (no cross-feature FK to crm.contacts — a separately transferable feature);
they are validated through ContactsService at the service boundary.

Migrations are discovered by folder presence (migrations/discovery.py), so this
runs whenever the feature folder is on disk — independent of ENABLED_MODULES,
which only toggles runtime wiring.

Revision ID: crm_deals0001
Revises: -
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "crm_deals0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("crm_deals",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def upgrade() -> None:
    op.create_table(
        "crm_deals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="UZS"),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=True),
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
            name="fk_crm_deals_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_crm_deals_amount_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_crm_deals"),
    )
    op.create_index("ix_crm_deals_tenant_id_stage", "crm_deals", ["tenant_id", "stage"])
    op.create_index("ix_crm_deals_tenant_id_contact_id", "crm_deals", ["tenant_id", "contact_id"])
    enable_tenant_rls("crm_deals")


def downgrade() -> None:
    disable_tenant_rls("crm_deals")
    op.drop_index("ix_crm_deals_tenant_id_contact_id", table_name="crm_deals")
    op.drop_index("ix_crm_deals_tenant_id_stage", table_name="crm_deals")
    op.drop_table("crm_deals")
