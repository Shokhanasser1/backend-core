"""Request-scoped CartService on the storefront bundle (buyer + shop, OV-39).

The buyer is authenticated but not a tenant member; ``storefront_bundle`` puts the
shop (from X-Shop-Tenant) in the tenant context and the buyer in the actor. The
cart is priced through ProductService (public interface of commerce.products).
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, storefront_bundle
from modules.commerce.cart.service import CartService
from modules.commerce.products import ProductService


async def cart_service(
    request: Request, bundle: ServiceBundle = Depends(storefront_bundle)
) -> CartService:
    products = ProductService(bundle.uow, request.app.state.bus, bundle.ctx)
    return CartService(bundle.uow, request.app.state.bus, bundle.ctx, products=products)
