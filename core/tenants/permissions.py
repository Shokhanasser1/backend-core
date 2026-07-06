"""Tenants permission catalog + system roles (interfaces §5.1, schema §2.2).

System roles (owner/admin/member) are DB rows with tenant_id NULL; their grants
are declared here in code and synced idempotently into role_permissions at
startup, so admin-API reads any role's permissions the same way.
"""

from collections.abc import Iterable, Mapping

from core.auth.permissions import PermissionDef

# --- permission codes owned by the tenants module ---
TENANT_READ = "tenants.tenant:read"
TENANT_UPDATE = "tenants.tenant:update"
MEMBER_READ = "tenants.member:read"
MEMBER_INVITE = "tenants.member:invite"
MEMBER_REMOVE = "tenants.member:remove"
MEMBER_UPDATE_ROLE = "tenants.member:update_role"
ROLE_READ = "tenants.role:read"
ROLE_MANAGE = "tenants.role:manage"

TENANTS_PERMISSIONS = [
    PermissionDef(TENANT_READ, "perm.tenants.tenant.read"),
    PermissionDef(TENANT_UPDATE, "perm.tenants.tenant.update"),
    PermissionDef(MEMBER_READ, "perm.tenants.member.read"),
    PermissionDef(MEMBER_INVITE, "perm.tenants.member.invite"),
    PermissionDef(MEMBER_REMOVE, "perm.tenants.member.remove"),
    PermissionDef(MEMBER_UPDATE_ROLE, "perm.tenants.member.update_role"),
    PermissionDef(ROLE_READ, "perm.tenants.role.read"),
    PermissionDef(ROLE_MANAGE, "perm.tenants.role.manage"),
]

# --- system roles ---
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

SYSTEM_ROLE_TITLES: dict[str, str] = {
    ROLE_OWNER: "role.owner",
    ROLE_ADMIN: "role.admin",
    ROLE_MEMBER: "role.member",
}

_ALL_TENANT_PERMISSIONS = frozenset(p.code for p in TENANTS_PERMISSIONS)


class SystemRoleGrants:
    """Default permission grants for the built-in system roles (owner/admin/member).

    tenants declares the baseline below; other core modules (billing, and future
    ones) contribute their own codes at startup via ``extend`` — this is how a
    module's permissions reach the default roles without tenants ever importing
    the module. ``sync_system_roles`` reconciles the resolved grants into
    ``role_permissions`` at startup (add missing, drop stale), so it is safe for
    modules to grow the set across app instances.
    """

    def __init__(self, roles: Iterable[str]) -> None:
        self._grants: dict[str, set[str]] = {role: set() for role in roles}

    def extend(self, grants: Mapping[str, Iterable[str]]) -> None:
        for role, codes in grants.items():
            if role not in self._grants:
                raise KeyError(f"unknown system role: {role!r}")
            self._grants[role].update(codes)

    def resolved(self) -> dict[str, frozenset[str]]:
        return {role: frozenset(codes) for role, codes in self._grants.items()}


# Process-global grants registry; modules extend it at startup.
system_role_grants = SystemRoleGrants((ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER))
system_role_grants.extend(
    {
        # Owner: everything (the last-owner invariant, not a permission, protects the seat).
        ROLE_OWNER: _ALL_TENANT_PERMISSIONS,
        # Admin: manage members and roles, read+update the tenant.
        ROLE_ADMIN: {
            TENANT_READ,
            TENANT_UPDATE,
            MEMBER_READ,
            MEMBER_INVITE,
            MEMBER_REMOVE,
            MEMBER_UPDATE_ROLE,
            ROLE_READ,
            ROLE_MANAGE,
        },
        # Member: read-only.
        ROLE_MEMBER: {TENANT_READ, MEMBER_READ, ROLE_READ},
    }
)
