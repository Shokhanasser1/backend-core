"""Admin permission catalog + default system-role grants (interfaces §5.1).

The scaffold owns a single permission — the right to read the admin menu. Each
screen declares its own permission in its module; admin does not grant those.
Owner and admin see the menu; a plain member does not (their menu would be empty
anyway — screens are gated individually).
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_OWNER, system_role_grants

# --- permission codes owned by the admin module ---
SCREEN_READ = "admin.screen:read"

ADMIN_PERMISSIONS = [
    PermissionDef(SCREEN_READ, "perm.admin.screen.read"),
]

ADMIN_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: frozenset({SCREEN_READ}),
    ROLE_ADMIN: frozenset({SCREEN_READ}),
}


def register_admin_rbac() -> None:
    """Register the admin permission and grant it to owner/admin. Idempotent."""
    register_permissions("admin", ADMIN_PERMISSIONS)
    system_role_grants.extend(ADMIN_SYSTEM_ROLE_GRANTS)
