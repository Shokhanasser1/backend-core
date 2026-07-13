"""crm.contacts permission catalog + default system-role grants.

Registered by the loader at startup (feature install()). The permission module is
the business module name without the feature (``crm``), per §5.1: a code belongs
to crm, not crm.contacts. Owner/admin fully manage the address book; members (the
sales reps) read/create/update people and companies but cannot delete them —
deletion is a destructive, org-owner concern.
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER, system_role_grants

COMPANY_READ = "crm.company:read"
COMPANY_CREATE = "crm.company:create"
COMPANY_UPDATE = "crm.company:update"
COMPANY_DELETE = "crm.company:delete"

CONTACT_READ = "crm.contact:read"
CONTACT_CREATE = "crm.contact:create"
CONTACT_UPDATE = "crm.contact:update"
CONTACT_DELETE = "crm.contact:delete"

CRM_CONTACTS_PERMISSIONS = [
    PermissionDef(COMPANY_READ, "perm.crm.company.read"),
    PermissionDef(COMPANY_CREATE, "perm.crm.company.create"),
    PermissionDef(COMPANY_UPDATE, "perm.crm.company.update"),
    PermissionDef(COMPANY_DELETE, "perm.crm.company.delete"),
    PermissionDef(CONTACT_READ, "perm.crm.contact.read"),
    PermissionDef(CONTACT_CREATE, "perm.crm.contact.create"),
    PermissionDef(CONTACT_UPDATE, "perm.crm.contact.update"),
    PermissionDef(CONTACT_DELETE, "perm.crm.contact.delete"),
]

_DELETE = frozenset({COMPANY_DELETE, CONTACT_DELETE})
_WORK = frozenset(
    {
        COMPANY_READ,
        COMPANY_CREATE,
        COMPANY_UPDATE,
        CONTACT_READ,
        CONTACT_CREATE,
        CONTACT_UPDATE,
    }
)
CRM_CONTACTS_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _WORK | _DELETE,
    ROLE_ADMIN: _WORK | _DELETE,
    ROLE_MEMBER: _WORK,
}


def register_crm_contacts_rbac() -> None:
    """Register the address-book permissions and grant them to the system roles.
    Idempotent (both registries dedupe)."""
    register_permissions("crm", CRM_CONTACTS_PERMISSIONS)
    system_role_grants.extend(CRM_CONTACTS_SYSTEM_ROLE_GRANTS)
