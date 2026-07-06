"""Tenants permission catalog + system roles (interfaces §5.1, schema §2.2).

System roles (owner/admin/member) are DB rows with tenant_id NULL; their grants
are declared here in code and synced idempotently into role_permissions at
startup, so admin-API reads any role's permissions the same way.
"""

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

# --- system roles and their grants ---
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

_ALL_TENANT_PERMISSIONS = frozenset(p.code for p in TENANTS_PERMISSIONS)

SYSTEM_ROLE_GRANTS: dict[str, frozenset[str]] = {
    # Owner: everything (the last-owner invariant, not a permission, protects the seat).
    ROLE_OWNER: _ALL_TENANT_PERMISSIONS,
    # Admin: manage members and roles, read+update the tenant.
    ROLE_ADMIN: frozenset(
        {
            TENANT_READ,
            TENANT_UPDATE,
            MEMBER_READ,
            MEMBER_INVITE,
            MEMBER_REMOVE,
            MEMBER_UPDATE_ROLE,
            ROLE_READ,
            ROLE_MANAGE,
        }
    ),
    # Member: read-only.
    ROLE_MEMBER: frozenset({TENANT_READ, MEMBER_READ, ROLE_READ}),
}

SYSTEM_ROLE_TITLES: dict[str, str] = {
    ROLE_OWNER: "role.owner",
    ROLE_ADMIN: "role.admin",
    ROLE_MEMBER: "role.member",
}
