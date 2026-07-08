"""saas.metering permission catalog + default system-role grants.

Registered by the loader at startup (feature install()). The permission module is
the business module name without the feature (``saas``), per §5.1. Owner/admin
read the tenant's usage; recording happens server-side via MeteringService (no
route), so it needs no permission.
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_OWNER, system_role_grants

USAGE_READ = "saas.usage:read"

SAAS_METERING_PERMISSIONS = [
    PermissionDef(USAGE_READ, "perm.saas.usage.read"),
]

_READ = frozenset({USAGE_READ})
SAAS_METERING_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _READ,
    ROLE_ADMIN: _READ,
}


def register_saas_metering_rbac() -> None:
    """Register the usage permission and grant it to owner/admin.
    Idempotent (both registries dedupe)."""
    register_permissions("saas", SAAS_METERING_PERMISSIONS)
    system_role_grants.extend(SAAS_METERING_SYSTEM_ROLE_GRANTS)
