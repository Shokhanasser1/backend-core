"""RLS policies for app_retention on audit_log (schema §2.5, decision OV-27).

Branch ``core_audit``. The base revision granted app_retention SELECT + DELETE on
audit_log but created RLS policies only for app_user and app_maintenance. With
RLS enabled and no policy for the role, PostgreSQL default-denies — app_retention
(NOBYPASSRLS) would see and delete zero rows. The retention sweep, which arrives
in Phase 4, runs as app_retention, so it needs explicit SELECT + DELETE policies.
Kept separate from maintenance (which has no DELETE grant): only this one role can
erase the journal, and only rows past the retention horizon are ever targeted by
the sweep — the policy itself stays permissive (USING true) because the horizon
lives in the job, not the row filter.

Revision ID: core_audit0002
Revises: core_audit0001
Create Date: 2026-07-07
"""

from collections.abc import Sequence

from alembic import op

from shared.db_provisioning import ROLE_RETENTION

revision: str = "core_audit0002"
down_revision: str | Sequence[str] | None = "core_audit0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        f"CREATE POLICY retention_read ON audit_log FOR SELECT TO {ROLE_RETENTION} USING (true)"
    )
    op.execute(
        f"CREATE POLICY retention_delete ON audit_log FOR DELETE TO {ROLE_RETENTION} USING (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS retention_delete ON audit_log")
    op.execute("DROP POLICY IF EXISTS retention_read ON audit_log")
