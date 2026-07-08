"""saas_plan_entitlements + saas_tenant_entitlements (feature saas.entitlements).

Branch ``saas_entitlements``. Two scopes (schema §1.2, §3.3):

- ``saas_plan_entitlements`` — GLOBAL reference table (the tariff grid): read-only
  for the runtime roles (GRANT SELECT), seeded by the client project. No RLS.
- ``saas_tenant_entitlements`` — TENANT-scoped with the standard tenant-isolation
  RLS (enable_tenant_rls): app_user sees/writes only its own tenant's row,
  app_maintenance bypasses for cross-tenant jobs. FK -> tenants ON DELETE RESTRICT.

Migrations are discovered by folder presence (migrations/discovery.py), so this
runs whenever the feature folder is on disk — independent of ENABLED_MODULES,
which only toggles runtime wiring.

Revision ID: saas_entitlements0001
Revises: -
Create Date: 2026-07-08
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_USER
from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "saas_entitlements0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("saas_entitlements",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "saas_plan_entitlements",
        sa.Column("plan_code", sa.Text(), nullable=False),
        sa.Column("entitlement_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("bool_value", sa.Boolean(), nullable=True),
        sa.Column("int_value", sa.BigInteger(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("kind IN ('flag', 'limit')", name="ck_saas_plan_entitlements_kind"),
        sa.CheckConstraint(
            "(kind = 'flag' AND bool_value IS NOT NULL AND int_value IS NULL) OR "
            "(kind = 'limit' AND bool_value IS NULL)",
            name="ck_saas_plan_entitlements_value_matches_kind",
        ),
        sa.CheckConstraint(
            "int_value IS NULL OR int_value >= 0",
            name="ck_saas_plan_entitlements_limit_non_negative",
        ),
        sa.PrimaryKeyConstraint("plan_code", "entitlement_key", name="pk_saas_plan_entitlements"),
    )

    op.create_table(
        "saas_tenant_entitlements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_code", sa.Text(), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_saas_tenant_entitlements_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saas_tenant_entitlements"),
    )
    op.create_index(
        "uq_saas_tenant_entitlements_tenant_id",
        "saas_tenant_entitlements",
        ["tenant_id"],
        unique=True,
    )

    # Global reference table: read-only for the runtime roles (seeded by the client).
    op.execute(f"GRANT SELECT ON saas_plan_entitlements TO {ROLE_USER}, {ROLE_MAINTENANCE}")
    enable_tenant_rls("saas_tenant_entitlements")


def downgrade() -> None:
    disable_tenant_rls("saas_tenant_entitlements")
    op.execute(f"REVOKE ALL ON saas_plan_entitlements FROM {ROLE_USER}, {ROLE_MAINTENANCE}")
    op.drop_index("uq_saas_tenant_entitlements_tenant_id", table_name="saas_tenant_entitlements")
    op.drop_table("saas_tenant_entitlements")
    op.drop_table("saas_plan_entitlements")
