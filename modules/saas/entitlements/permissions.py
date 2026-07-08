"""saas.entitlements permission catalog + default system-role grants.

Registered by the loader at startup (feature install()). The permission module is
the business module name without the feature (``saas``), per §5.1. Only owner/
admin read the tenant's entitlement overview; members do not (enforcement of
numeric limits happens through EntitlementService inside the calling feature, not
through this route).
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_OWNER, system_role_grants

ENTITLEMENT_READ = "saas.entitlement:read"

SAAS_ENTITLEMENTS_PERMISSIONS = [
    PermissionDef(ENTITLEMENT_READ, "perm.saas.entitlement.read"),
]

_READ = frozenset({ENTITLEMENT_READ})
SAAS_ENTITLEMENTS_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _READ,
    ROLE_ADMIN: _READ,
}


def register_saas_entitlements_rbac() -> None:
    """Register the entitlement permissions and grant them to owner/admin.
    Idempotent (both registries dedupe)."""
    register_permissions("saas", SAAS_ENTITLEMENTS_PERMISSIONS)
    system_role_grants.extend(SAAS_ENTITLEMENTS_SYSTEM_ROLE_GRANTS)
