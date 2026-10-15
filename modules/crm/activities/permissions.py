"""crm.activities permission catalog + default system-role grants.

Registered by the loader at startup (feature install()). The permission module is
the business module name without the feature (``crm``), per §5.1. Owner/admin fully
manage activities; members (sales reps) read/create/update them — completing and
reopening are updates — but cannot delete (same split as crm.contacts/crm.deals).
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER, system_role_grants

ACTIVITY_READ = "crm.activity:read"
ACTIVITY_CREATE = "crm.activity:create"
ACTIVITY_UPDATE = "crm.activity:update"
ACTIVITY_DELETE = "crm.activity:delete"

CRM_ACTIVITIES_PERMISSIONS = [
    PermissionDef(ACTIVITY_READ, "perm.crm.activity.read"),
    PermissionDef(ACTIVITY_CREATE, "perm.crm.activity.create"),
    PermissionDef(ACTIVITY_UPDATE, "perm.crm.activity.update"),
    PermissionDef(ACTIVITY_DELETE, "perm.crm.activity.delete"),
]

_WORK = frozenset({ACTIVITY_READ, ACTIVITY_CREATE, ACTIVITY_UPDATE})
CRM_ACTIVITIES_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _WORK | {ACTIVITY_DELETE},
    ROLE_ADMIN: _WORK | {ACTIVITY_DELETE},
    ROLE_MEMBER: _WORK,
}


def register_crm_activities_rbac() -> None:
    """Register the activity permissions and grant them to the system roles.
    Idempotent (both registries dedupe)."""
    register_permissions("crm", CRM_ACTIVITIES_PERMISSIONS)
    system_role_grants.extend(CRM_ACTIVITIES_SYSTEM_ROLE_GRANTS)
