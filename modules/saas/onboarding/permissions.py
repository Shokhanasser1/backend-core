"""saas.onboarding permission catalog + default system-role grants.

Registered by the loader at startup (feature install()). The permission module is
the business module name without the feature (``saas``), per §5.1. Owner/admin read
the checklist and mark steps done (activation is an org-setup concern).
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_OWNER, system_role_grants

ONBOARDING_READ = "saas.onboarding:read"
ONBOARDING_UPDATE = "saas.onboarding:update"

SAAS_ONBOARDING_PERMISSIONS = [
    PermissionDef(ONBOARDING_READ, "perm.saas.onboarding.read"),
    PermissionDef(ONBOARDING_UPDATE, "perm.saas.onboarding.update"),
]

_MANAGE = frozenset({ONBOARDING_READ, ONBOARDING_UPDATE})
SAAS_ONBOARDING_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _MANAGE,
    ROLE_ADMIN: _MANAGE,
}


def register_saas_onboarding_rbac() -> None:
    """Register the onboarding permissions and grant them to owner/admin.
    Idempotent (both registries dedupe)."""
    register_permissions("saas", SAAS_ONBOARDING_PERMISSIONS)
    system_role_grants.extend(SAAS_ONBOARDING_SYSTEM_ROLE_GRANTS)
