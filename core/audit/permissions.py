"""Audit permission catalog + default system-role grants (interfaces §5.1).

Reading the audit log exposes what other members did, so it is an owner/admin
capability, not a member one. The audit admin screen (admin.py) is gated by this
same code.
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_OWNER, system_role_grants

# --- permission codes owned by the audit module ---
RECORD_READ = "audit.record:read"

AUDIT_PERMISSIONS = [
    PermissionDef(RECORD_READ, "perm.audit.record.read"),
]

AUDIT_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: frozenset({RECORD_READ}),
    ROLE_ADMIN: frozenset({RECORD_READ}),
}


def register_audit_rbac() -> None:
    """Register the audit permission and grant it to owner/admin. Idempotent."""
    register_permissions("audit", AUDIT_PERMISSIONS)
    system_role_grants.extend(AUDIT_SYSTEM_ROLE_GRANTS)
