"""Notification tables: notification_settings, notification_outbox.

Branch ``core_notifications`` (schema §2.4). notification_settings is
tenant-scoped (standard RLS). notification_outbox is hybrid (tenant_id NULL =
platform send: email verification, password reset): app_user reads/writes only
its own tenant's rows, app_maintenance runs the cross-tenant dispatcher and
retention sweep (§3.1) — so it also inserts platform rows and gets DELETE.

Revision ID: core_notifications0001
Revises: -
Create Date: 2026-07-07
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_USER
from shared.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "core_notifications0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("core_notifications",)
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
    _create_tables()
    _apply_rls()


def _create_tables() -> None:
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("config_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_notification_settings_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_settings"),
    )
    op.create_index(
        "uq_notification_settings_tenant_id_channel",
        "notification_settings",
        ["tenant_id", "channel"],
        unique=True,
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("recipient", sa.Text(), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), nullable=False, server_default="ru"),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending','sending','sent','failed','dead')",
            name="ck_notification_outbox_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_notification_outbox_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_outbox"),
    )
    # Idempotency of send(): NULLS NOT DISTINCT (PG16) so platform sends
    # (tenant_id NULL) dedup too; channel in the key (one row per channel).
    op.create_index(
        "uq_notification_outbox_tenant_id_dedup_key_channel",
        "notification_outbox",
        ["tenant_id", "dedup_key", "channel"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )
    # Dispatcher's main query (SELECT ... FOR UPDATE SKIP LOCKED).
    op.create_index(
        "ix_notification_outbox_due",
        "notification_outbox",
        ["next_retry_at"],
        postgresql_where=sa.text("status IN ('pending','failed','sending')"),
    )
    op.create_index(
        "ix_notification_outbox_notification_id", "notification_outbox", ["notification_id"]
    )
    op.create_index(
        "ix_notification_outbox_tenant_id_created_at",
        "notification_outbox",
        ["tenant_id", "created_at"],
    )


def _apply_rls() -> None:
    # Tenant-scoped: standard isolation. app_maintenance (maintenance_all) reads
    # channel configs cross-tenant for the dispatcher.
    enable_tenant_rls("notification_settings")

    # Hybrid outbox: app_user reads/writes only its own tenant's rows;
    # app_maintenance owns platform sends (tenant_id NULL), the cross-tenant
    # dispatcher (UPDATE) and the retention sweep (DELETE, §3.1).
    op.execute("ALTER TABLE notification_outbox ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON notification_outbox FOR ALL TO {ROLE_USER} "
        f"USING (tenant_id = app_current_tenant_id()) "
        f"WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        f"CREATE POLICY maintenance_all ON notification_outbox FOR ALL TO {ROLE_MAINTENANCE} "
        f"USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT, INSERT ON notification_outbox TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON notification_outbox TO {ROLE_MAINTENANCE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON notification_outbox FROM {ROLE_USER}")
    op.execute(f"REVOKE ALL ON notification_outbox FROM {ROLE_MAINTENANCE}")
    op.drop_table("notification_outbox")
    disable_tenant_rls("notification_settings")
    op.drop_table("notification_settings")
