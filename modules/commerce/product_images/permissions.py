"""Permission catalog for commerce.product_images (registered under the commerce
module namespace, §5.1). Owner/admin manage images; member reads.
"""

from core.auth.access_service import register_permissions
from core.auth.permissions import PermissionDef
from core.tenants.permissions import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    system_role_grants,
)

PRODUCT_IMAGE_READ = "commerce.product_image:read"
PRODUCT_IMAGE_MANAGE = "commerce.product_image:manage"

PRODUCT_IMAGES_PERMISSIONS = [
    PermissionDef(PRODUCT_IMAGE_READ, "perm.commerce.product_image.read"),
    PermissionDef(PRODUCT_IMAGE_MANAGE, "perm.commerce.product_image.manage"),
]

_MANAGE = frozenset({PRODUCT_IMAGE_READ, PRODUCT_IMAGE_MANAGE})
PRODUCT_IMAGES_SYSTEM_ROLE_GRANTS = {
    ROLE_OWNER: _MANAGE,
    ROLE_ADMIN: _MANAGE,
    ROLE_MEMBER: frozenset({PRODUCT_IMAGE_READ}),
}


def register_product_images_rbac() -> None:
    register_permissions("commerce", PRODUCT_IMAGES_PERMISSIONS)
    system_role_grants.extend(PRODUCT_IMAGES_SYSTEM_ROLE_GRANTS)
