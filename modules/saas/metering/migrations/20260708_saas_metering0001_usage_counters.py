"""saas_usage_counters table (feature saas.metering). Branch saas_metering.

Tenant-scoped daily usage counters with the standard tenant-isolation RLS
(enable_tenant_rls): app_user records/reads only its own tenant's rows,
app_maintenance bypasses for the cross-tenant retention sweep. One row per
(tenant, metric, day) — enforced by a unique index that also serves as the
UPSERT conflict target and supports per-metric range queries. FK -> tenants
ON DELETE RESTRICT.

Revision ID: saas_metering0001
Revises: -
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "saas_metering0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("saas_metering",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def upgrade() -> None:
    op.create_table(
        "saas_usage_counters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("metric_key", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Date(), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_saas_usage_counters_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("value >= 0", name="ck_saas_usage_counters_value_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_saas_usage_counters"),
    )
    op.create_index(
        "uq_saas_usage_counters_tenant_id_metric_key_bucket",
        "saas_usage_counters",
        ["tenant_id", "metric_key", "bucket"],
        unique=True,
    )
    enable_tenant_rls("saas_usage_counters")


def downgrade() -> None:
    disable_tenant_rls("saas_usage_counters")
    op.drop_index(
        "uq_saas_usage_counters_tenant_id_metric_key_bucket", table_name="saas_usage_counters"
    )
    op.drop_table("saas_usage_counters")
