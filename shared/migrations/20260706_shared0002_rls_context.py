"""RLS context helper functions + DB-role presence check (schema §3.2, §3.6).

Continues the ``shared`` branch. Creates ``app_current_tenant_id()`` /
``app_current_user_id()`` used by every tenant RLS policy, and fails with a
clear error if the cluster-level roles were not provisioned (they are created
by the environment bootstrap, not by this migration — schema §3.6).

Revision ID: shared0002
Revises: shared0001
Create Date: 2026-07-06
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from shared.db_provisioning import ALL_ROLES, ROLE_MAINTENANCE, ROLE_USER

revision: str = "shared0002"
down_revision: str | Sequence[str] | None = "shared0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _check_roles_present() -> None:
    bind = op.get_bind()
    existing = {
        row[0]
        for row in bind.execute(
            text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
            {"names": list(ALL_ROLES)},
        )
    }
    missing = [role for role in ALL_ROLES if role not in existing]
    if missing:
        raise RuntimeError(
            "Required DB roles are missing: "
            + ", ".join(missing)
            + ". Provision them with the environment bootstrap before migrating "
            "(docker compose init script for dev; see schema §3.6)."
        )


def upgrade() -> None:
    _check_roles_present()
    op.execute(
        "CREATE FUNCTION app_current_tenant_id() RETURNS uuid LANGUAGE sql STABLE "
        "AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$"
    )
    op.execute(
        "CREATE FUNCTION app_current_user_id() RETURNS uuid LANGUAGE sql STABLE "
        "AS $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$"
    )
    # processed_events (shared0001) is a global service table without RLS. The
    # reliable dispatcher runs as app_user OR app_maintenance (maintenance sinks
    # such as audit) and needs read/write; app_maintenance also runs the
    # retention sweep and needs DELETE (schema §2.7). Grants are issued here
    # because Phase 2 introduces the role separation.
    op.execute(f"GRANT SELECT, INSERT ON processed_events TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON processed_events TO {ROLE_MAINTENANCE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON processed_events FROM {ROLE_MAINTENANCE}")
    op.execute(f"REVOKE ALL ON processed_events FROM {ROLE_USER}")
    op.execute("DROP FUNCTION IF EXISTS app_current_user_id()")
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant_id()")
