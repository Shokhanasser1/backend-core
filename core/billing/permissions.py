"""Billing permission catalog + default system-role grants (interfaces §5.1).

Billing is the first module beyond tenants to gate its endpoints, so it both
registers its permission codes and contributes their grants to the built-in
system roles (owner/admin manage; member reads) via the tenants grants registry.
``register_billing_rbac`` is called once at startup, before the route validator
and the system-role sync.
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    system_role_grants,
)

# --- permission codes owned by the billing module ---
PLAN_READ = "billing.plan:read"
SUBSCRIPTION_READ = "billing.subscription:read"
SUBSCRIPTION_MANAGE = "billing.subscription:manage"

BILLING_PERMISSIONS = [
    PermissionDef(PLAN_READ, "perm.billing.plan.read"),
    PermissionDef(SUBSCRIPTION_READ, "perm.billing.subscription.read"),
    PermissionDef(SUBSCRIPTION_MANAGE, "perm.billing.subscription.manage"),
]

_MANAGE = frozenset({PLAN_READ, SUBSCRIPTION_READ, SUBSCRIPTION_MANAGE})
BILLING_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _MANAGE,
    ROLE_ADMIN: _MANAGE,
    ROLE_MEMBER: frozenset({PLAN_READ, SUBSCRIPTION_READ}),
}


def register_billing_rbac() -> None:
    """Register billing permissions and grant them to the system roles. Idempotent
    (safe to call per app instance) — both the catalog and the grants registry
    dedupe."""
    register_permissions("billing", BILLING_PERMISSIONS)
    system_role_grants.extend(BILLING_SYSTEM_ROLE_GRANTS)
