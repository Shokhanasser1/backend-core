"""Append-only audit_log table (schema §2.5, decision OV-26).

Branch ``core_audit``. Append-only is enforced by grants: runtime roles get
SELECT + INSERT only (no UPDATE to anyone but the absent owner; DELETE only to
app_retention). Hybrid RLS: app_user reads/writes its own tenant's rows;
app_maintenance (the bus sink) writes system rows (tenant_id NULL) too.

Revision ID: core_audit0001
Revises: -
Create Date: 2026-07-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, JSONB

from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_RETENTION, ROLE_USER

revision: str = "core_audit0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("core_audit",)
depends_on: str | Sequence[str] | None = "shared0002"


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text(), nullable=True),
        sa.Column("object_id", sa.Text(), nullable=True),
        sa.Column("ip", INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index(
        "uq_audit_log_event_id",
        "audit_log",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("event_id IS NOT NULL"),
    )
    op.create_index(
        "ix_audit_log_tenant_id_created_at",
        "audit_log",
        ["tenant_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_log_tenant_object",
        "audit_log",
        ["tenant_id", "object_type", "object_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_log_tenant_user",
        "audit_log",
        ["tenant_id", "user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_log_created_at_brin", "audit_log", ["created_at"], postgresql_using="brin"
    )

    # Hybrid RLS (schema §3.3): app_user sees/writes only its own tenant's rows;
    # app_maintenance (bus sink) writes system rows too.
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_read ON audit_log FOR SELECT TO app_user "
        "USING (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        "CREATE POLICY tenant_insert ON audit_log FOR INSERT TO app_user "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        "CREATE POLICY maintenance_all ON audit_log FOR ALL TO app_maintenance "
        "USING (true) WITH CHECK (true)"
    )

    # Append-only grants: SELECT + INSERT only; DELETE only for app_retention;
    # nobody gets UPDATE (schema §2.5).
    op.execute(f"GRANT SELECT, INSERT ON audit_log TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT ON audit_log TO {ROLE_MAINTENANCE}")
    op.execute(f"GRANT SELECT, DELETE ON audit_log TO {ROLE_RETENTION}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON audit_log FROM {ROLE_RETENTION}")
    op.execute(f"REVOKE ALL ON audit_log FROM {ROLE_MAINTENANCE}")
    op.execute(f"REVOKE ALL ON audit_log FROM {ROLE_USER}")
    op.drop_table("audit_log")
