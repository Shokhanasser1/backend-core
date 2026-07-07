"""commerce.products permission catalog + default system-role grants.

Registered by the loader at startup (feature install()). The permission module
is the business module name without the feature (``commerce``), per §5.1: a code
belongs to commerce, not commerce.products. Owner/admin manage the catalog;
members read it.
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER, system_role_grants

PRODUCT_READ = "commerce.product:read"
PRODUCT_CREATE = "commerce.product:create"
PRODUCT_UPDATE = "commerce.product:update"

PRODUCTS_PERMISSIONS = [
    PermissionDef(PRODUCT_READ, "perm.commerce.product.read"),
    PermissionDef(PRODUCT_CREATE, "perm.commerce.product.create"),
    PermissionDef(PRODUCT_UPDATE, "perm.commerce.product.update"),
]

_MANAGE = frozenset({PRODUCT_READ, PRODUCT_CREATE, PRODUCT_UPDATE})
PRODUCTS_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _MANAGE,
    ROLE_ADMIN: _MANAGE,
    ROLE_MEMBER: frozenset({PRODUCT_READ}),
}


def register_products_rbac() -> None:
    """Register the catalog permissions and grant them to the system roles.
    Idempotent (both registries dedupe)."""
    register_permissions("commerce", PRODUCTS_PERMISSIONS)
    system_role_grants.extend(PRODUCTS_SYSTEM_ROLE_GRANTS)
