"""crm.deals permission catalog + default system-role grants.

Registered by the loader at startup (feature install()). The permission module is
the business module name without the feature (``crm``), per §5.1. Owner/admin fully
manage the pipeline; members (sales reps) read/create/update deals but cannot delete
them — deletion is an org-owner concern (same split as crm.contacts).
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER, system_role_grants

DEAL_READ = "crm.deal:read"
DEAL_CREATE = "crm.deal:create"
DEAL_UPDATE = "crm.deal:update"
DEAL_DELETE = "crm.deal:delete"

CRM_DEALS_PERMISSIONS = [
    PermissionDef(DEAL_READ, "perm.crm.deal.read"),
    PermissionDef(DEAL_CREATE, "perm.crm.deal.create"),
    PermissionDef(DEAL_UPDATE, "perm.crm.deal.update"),
    PermissionDef(DEAL_DELETE, "perm.crm.deal.delete"),
]

_WORK = frozenset({DEAL_READ, DEAL_CREATE, DEAL_UPDATE})
CRM_DEALS_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _WORK | {DEAL_DELETE},
    ROLE_ADMIN: _WORK | {DEAL_DELETE},
    ROLE_MEMBER: _WORK,
}


def register_crm_deals_rbac() -> None:
    """Register the pipeline permissions and grant them to the system roles.
    Idempotent (both registries dedupe)."""
    register_permissions("crm", CRM_DEALS_PERMISSIONS)
    system_role_grants.extend(CRM_DEALS_SYSTEM_ROLE_GRANTS)
