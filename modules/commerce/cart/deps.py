"""Request-scoped CartService on the storefront bundle (buyer + shop, OV-39).

The buyer is authenticated but not a tenant member; ``storefront_bundle`` puts the
shop (from X-Shop-Tenant) in the tenant context and the buyer in the actor. The
cart is priced through ProductService (public interface of commerce.products) and
checks out through OrderService (public dependency of commerce.orders) — both on
the SAME bundle, so the order and the closed cart commit in one transaction.
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, storefront_bundle
from modules.commerce.cart.service import CartService
from modules.commerce.orders import OrderService, order_service
from modules.commerce.products import ProductService


async def cart_service(
    request: Request,
    bundle: ServiceBundle = Depends(storefront_bundle),
    orders: OrderService = Depends(order_service),
) -> CartService:
    products = ProductService(bundle.uow, request.app.state.bus, bundle.ctx)
    return CartService(
        bundle.uow, request.app.state.bus, bundle.ctx, products=products, orders=orders
    )
