"""RLS migration helpers (schema doc §3.3, §3.6, §4).

A feature/component migration turns on tenant isolation for one of its tables
with a single call, and its downgrade mirrors it with one call — so the policy
never drifts across client projects. Table/role names are developer constants
(validated as identifiers), never user input: no injection surface.

The helpers assume the two context functions created by the base core
migration: ``app_current_tenant_id()`` and ``app_current_user_id()``.
"""

import re

from alembic import op

from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_USER

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


def grant_crud(table: str, *roles: str) -> None:
    table = _ident(table)
    grantees = ", ".join(_ident(r) for r in roles)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {grantees}")


def revoke_all(table: str, *roles: str) -> None:
    table = _ident(table)
    grantees = ", ".join(_ident(r) for r in roles)
    op.execute(f"REVOKE ALL ON {table} FROM {grantees}")


def enable_tenant_rls(table: str) -> None:
    """Standard tenant-isolation policy for a table with a non-null tenant_id:
    app_user sees/writes only its own tenant's rows; app_maintenance bypasses
    the filter (exhaustive cross-tenant operations, §3.4)."""
    table = _ident(table)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL TO {ROLE_USER} "
        f"USING (tenant_id = app_current_tenant_id()) "
        f"WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        f"CREATE POLICY maintenance_all ON {table} FOR ALL TO {ROLE_MAINTENANCE} "
        f"USING (true) WITH CHECK (true)"
    )
    grant_crud(table, ROLE_USER, ROLE_MAINTENANCE)


def disable_tenant_rls(table: str) -> None:
    table = _ident(table)
    revoke_all(table, ROLE_USER, ROLE_MAINTENANCE)
    op.execute(f"DROP POLICY IF EXISTS maintenance_all ON {table}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
