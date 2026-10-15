"""commerce.orders — orders + payment + receipt (requires commerce.products).

The loader mounts ``router`` (storefront) and calls ``install()``, which registers
RBAC, the order-paid template + bus subscribers, and the admin screen. The admin
screen router is mounted separately under /api/admin/orders by mount_admin_screens.

Public interface for siblings (§1.2): ``OrderService`` + its DTOs, and the
``order_service`` dependency (a storefront feature — commerce.cart — places an
order in the same request through it).
"""

from core.admin.registry import admin_registry
from modules.commerce.orders.admin import ORDERS_SCREEN
from modules.commerce.orders.deps import order_service
from modules.commerce.orders.permissions import register_orders_rbac
from modules.commerce.orders.router import router
from modules.commerce.orders.schemas import OrderCheckoutDTO, OrderLineIn
from modules.commerce.orders.service import OrderService

__all__ = [
    "OrderCheckoutDTO",
    "OrderLineIn",
    "OrderService",
    "install",
    "order_service",
    "router",
]


def install() -> None:
    register_orders_rbac()
    import modules.commerce.orders.subscribers  # noqa: F401  (templates + bus subscribers)

    admin_registry.register(ORDERS_SCREEN)
