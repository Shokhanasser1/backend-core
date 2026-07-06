"""Tenant tables: tenants, roles, role_permissions, memberships, invitations.

Branch ``core_tenants`` (schema §2.2, RLS policies §3.3). Depends on core_auth
(FK to users) and shared0002 (RLS helper functions).

Revision ID: core_tenants0001
Revises: -
Create Date: 2026-07-06
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_USER

revision: str = "core_tenants0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("core_tenants",)
depends_on: str | Sequence[str] | None = ("core_auth0001", "shared0002")

_TABLES = ("invitations", "memberships", "role_permissions", "roles", "tenants")


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
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("default_locale", sa.Text(), nullable=False, server_default="ru"),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'deleted')", name="ck_tenants_status"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_tenants_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )
    op.create_index("uq_tenants_slug", "tenants", ["slug"], unique=True)
    op.create_index("ix_tenants_owner_user_id", "tenants", ["owner_user_id"])

    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_roles_tenant_id_tenants", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"])
    # PG16 UNIQUE NULLS NOT DISTINCT: no two system roles share a code.
    op.create_index(
        "uq_roles_tenant_id_code",
        "roles",
        ["tenant_id", "code"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_code", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_role_permissions_role_id_roles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_code", name="pk_role_permissions"),
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        *_timestamps(),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_memberships_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_memberships_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_memberships_role_id_roles", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
    )
    op.create_index(
        "uq_memberships_tenant_id_user_id", "memberships", ["tenant_id", "user_id"], unique=True
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_index("ix_memberships_role_id", "memberships", ["role_id"])

    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')", name="ck_invitations_status"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_invitations_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_invitations_role_id_roles", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_invitations_invited_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name="fk_invitations_accepted_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
    )
    op.create_index("uq_invitations_token_hash", "invitations", ["token_hash"], unique=True)
    op.create_index("ix_invitations_tenant_id_status", "invitations", ["tenant_id", "status"])
    op.create_index("ix_invitations_role_id", "invitations", ["role_id"])
    op.create_index(
        "uq_invitations_pending_email",
        "invitations",
        ["tenant_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def _apply_rls() -> None:
    # tenants: read own + tenants I'm a member of; update only my current tenant.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY member_visible ON tenants FOR SELECT TO app_user "
        "USING (id = app_current_tenant_id() OR EXISTS ("
        "SELECT 1 FROM memberships m WHERE m.tenant_id = tenants.id "
        "AND m.user_id = app_current_user_id()))"
    )
    op.execute(
        "CREATE POLICY tenant_self_update ON tenants FOR UPDATE TO app_user "
        "USING (id = app_current_tenant_id()) WITH CHECK (id = app_current_tenant_id())"
    )
    op.execute(
        "CREATE POLICY maintenance_all ON tenants FOR ALL TO app_maintenance "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT, UPDATE ON tenants TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenants TO {ROLE_MAINTENANCE}")

    # roles: read system + own-tenant rows; write only own-tenant custom rows.
    op.execute("ALTER TABLE roles ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY hybrid_read ON roles FOR SELECT TO app_user "
        "USING (tenant_id IS NULL OR tenant_id = app_current_tenant_id())"
    )
    op.execute(
        "CREATE POLICY hybrid_write ON roles FOR ALL TO app_user "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        "CREATE POLICY maintenance_all ON roles FOR ALL TO app_maintenance "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON roles TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON roles TO {ROLE_MAINTENANCE}")

    # role_permissions: read grants of system + own roles; write only own-tenant.
    op.execute("ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY via_role_read ON role_permissions FOR SELECT TO app_user "
        "USING (EXISTS (SELECT 1 FROM roles r WHERE r.id = role_id "
        "AND (r.tenant_id IS NULL OR r.tenant_id = app_current_tenant_id())))"
    )
    op.execute(
        "CREATE POLICY via_role_insert ON role_permissions FOR INSERT TO app_user "
        "WITH CHECK (EXISTS (SELECT 1 FROM roles r WHERE r.id = role_id "
        "AND r.tenant_id = app_current_tenant_id()))"
    )
    op.execute(
        "CREATE POLICY via_role_delete ON role_permissions FOR DELETE TO app_user "
        "USING (EXISTS (SELECT 1 FROM roles r WHERE r.id = role_id "
        "AND r.tenant_id = app_current_tenant_id()))"
    )
    op.execute(
        "CREATE POLICY maintenance_all ON role_permissions FOR ALL TO app_maintenance "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON role_permissions TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON role_permissions TO {ROLE_MAINTENANCE}")

    # memberships: read own-tenant + my own rows; write only own-tenant (no
    # app_user INSERT grant — creation is a cross-tenant maintenance operation).
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_or_own ON memberships FOR SELECT TO app_user "
        "USING (tenant_id = app_current_tenant_id() OR user_id = app_current_user_id())"
    )
    op.execute(
        "CREATE POLICY tenant_write ON memberships FOR ALL TO app_user "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        "CREATE POLICY maintenance_all ON memberships FOR ALL TO app_maintenance "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT, UPDATE, DELETE ON memberships TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON memberships TO {ROLE_MAINTENANCE}")

    # invitations: standard tenant isolation (accept happens via maintenance).
    op.execute("ALTER TABLE invitations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON invitations FOR ALL TO app_user "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        "CREATE POLICY maintenance_all ON invitations FOR ALL TO app_maintenance "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON invitations TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON invitations TO {ROLE_MAINTENANCE}")


def downgrade() -> None:
    # member_visible on tenants references memberships, which is dropped first —
    # remove the cross-table policy before the tables.
    op.execute("DROP POLICY IF EXISTS member_visible ON tenants")
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {ROLE_USER}")
        op.execute(f"REVOKE ALL ON {table} FROM {ROLE_MAINTENANCE}")
    op.drop_table("invitations")
    op.drop_table("memberships")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_table("tenants")
