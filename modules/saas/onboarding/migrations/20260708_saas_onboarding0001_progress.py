"""saas_onboarding_progress table (feature saas.onboarding). Branch saas_onboarding.

Tenant-scoped checklist progress with the standard tenant-isolation RLS
(enable_tenant_rls): app_user reads/writes only its own tenant's rows,
app_maintenance bypasses for cross-tenant jobs. One row per completed step
(unique per tenant + step_key). FK -> tenants ON DELETE RESTRICT.

Revision ID: saas_onboarding0001
Revises: -
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "saas_onboarding0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("saas_onboarding",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def upgrade() -> None:
    op.create_table(
        "saas_onboarding_progress",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_saas_onboarding_progress_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saas_onboarding_progress"),
    )
    op.create_index(
        "uq_saas_onboarding_progress_tenant_id_step_key",
        "saas_onboarding_progress",
        ["tenant_id", "step_key"],
        unique=True,
    )
    enable_tenant_rls("saas_onboarding_progress")


def downgrade() -> None:
    disable_tenant_rls("saas_onboarding_progress")
    op.drop_index(
        "uq_saas_onboarding_progress_tenant_id_step_key", table_name="saas_onboarding_progress"
    )
    op.drop_table("saas_onboarding_progress")
