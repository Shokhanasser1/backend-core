"""Files permission catalog + default system-role grants (interfaces §5.1).

Owner/admin manage files (upload/read/delete); member reads. ``register_files_rbac``
is called once at startup (app/main.py), before the route validator and the
system-role sync. Idempotent — both registries dedupe.
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    system_role_grants,
)

# --- permission codes owned by the files module ---
FILE_READ = "files.file:read"
FILE_UPLOAD = "files.file:upload"
FILE_DELETE = "files.file:delete"

FILES_PERMISSIONS = [
    PermissionDef(FILE_READ, "perm.files.file.read"),
    PermissionDef(FILE_UPLOAD, "perm.files.file.upload"),
    PermissionDef(FILE_DELETE, "perm.files.file.delete"),
]

_MANAGE = frozenset({FILE_READ, FILE_UPLOAD, FILE_DELETE})
FILES_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _MANAGE,
    ROLE_ADMIN: _MANAGE,
    ROLE_MEMBER: frozenset({FILE_READ}),
}


def register_files_rbac() -> None:
    """Register files permissions and grant them to the system roles."""
    register_permissions("files", FILES_PERMISSIONS)
    system_role_grants.extend(FILES_SYSTEM_ROLE_GRANTS)
