"""Cluster-level DB roles (schema doc §3.1, §3.6).

Roles are cluster objects, not database objects — they cannot honestly be
created/rolled back by a per-database Alembic migration. They are provisioned
by an environment bootstrap: the init script in docker-compose for dev, a
provisioning document for prod (docs/DEPLOYMENT — Phase 5), and a fixture for
tests. The base core migration only *checks* that the roles exist and fails
with a clear error otherwise.

Security property this enables: runtime connects as ``app_user`` — a role that
neither owns the tables nor has BYPASSRLS — so RLS applies to every runtime
query (threat model V1, second line). Migrations connect as ``app_migrator``
(owner). ``app_maintenance`` runs the exhaustive list of cross-tenant
operations; ``app_retention`` only deletes ``audit_log`` rows.
"""

import re

ROLE_MIGRATOR = "app_migrator"
ROLE_USER = "app_user"
ROLE_MAINTENANCE = "app_maintenance"
ROLE_RETENTION = "app_retention"

RUNTIME_ROLES = (ROLE_USER, ROLE_MAINTENANCE, ROLE_RETENTION)
ALL_ROLES = (ROLE_MIGRATOR, *RUNTIME_ROLES)

# Dev/test passwords — obviously non-secret, overridden by the environment in
# any real deployment (12-factor). Kept alphanumeric so they are safe to inline
# into the bootstrap DDL below.
DEV_ROLE_PASSWORDS: dict[str, str] = {role: role for role in ALL_ROLES}

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_PASSWORD_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _check_role(role: str) -> None:
    if not _IDENT_RE.match(role):
        raise ValueError(f"invalid role identifier: {role!r}")


def render_role_bootstrap_statements(passwords: dict[str, str] | None = None) -> list[str]:
    """Idempotent DDL as a list of individual statements — the driver runs them
    one at a time (asyncpg's prepared-statement protocol rejects multi-command
    strings). Must be executed by a superuser. Passwords are validated as
    alphanumeric before inlining — deployment constants, never user input
    (no injection surface)."""
    pw = passwords or DEV_ROLE_PASSWORDS
    statements: list[str] = []
    for role in ALL_ROLES:
        _check_role(role)
        password = pw[role]
        if not _PASSWORD_RE.match(password):
            raise ValueError(f"role password for {role!r} must be alphanumeric")
        # NOSUPERUSER + NOBYPASSRLS on every runtime role so RLS is enforced;
        # app_migrator owns the schema but is not a superuser either.
        statements.append(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
            f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS NOINHERIT; "
            f"END IF; END $$"
        )
    # app_migrator owns/creates objects in schema public (PG15+ revokes CREATE
    # from PUBLIC); runtime roles only need USAGE (per-table grants are issued
    # by migrations / enable_tenant_rls).
    statements.append(f"GRANT CREATE, USAGE ON SCHEMA public TO {ROLE_MIGRATOR}")
    statements.append(
        f"GRANT USAGE ON SCHEMA public TO {ROLE_USER}, {ROLE_MAINTENANCE}, {ROLE_RETENTION}"
    )
    return statements


def render_role_bootstrap_sql(passwords: dict[str, str] | None = None) -> str:
    """The same DDL as a single script (for docs / a psql init file that
    supports multi-statement input)."""
    return ";\n".join(render_role_bootstrap_statements(passwords)) + ";"
