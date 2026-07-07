"""commerce.orders permission catalog + default system-role grants.

Only staff read the order list (owner/admin); buyers reach their own orders via
the storefront (ownership, not RBAC).
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import ROLE_ADMIN, ROLE_OWNER, system_role_grants

ORDER_READ = "commerce.order:read"

ORDERS_PERMISSIONS = [
    PermissionDef(ORDER_READ, "perm.commerce.order.read"),
]

ORDERS_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: frozenset({ORDER_READ}),
    ROLE_ADMIN: frozenset({ORDER_READ}),
}


def register_orders_rbac() -> None:
    register_permissions("commerce", ORDERS_PERMISSIONS)
    system_role_grants.extend(ORDERS_SYSTEM_ROLE_GRANTS)
