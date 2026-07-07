"""Storefront cart endpoints (/api/commerce/cart). Buyer-facing: every route
carries ``authenticated_endpoint`` (OV-39) — the buyer is not a tenant member, so
tenant RBAC does not apply; ownership is enforced in the service. The shop is
addressed by the X-Shop-Tenant header (storefront_bundle).
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from core.auth.deps import authenticated_endpoint
from modules.commerce.cart.deps import cart_service
from modules.commerce.cart.schemas import AddItemIn, CartDTO
from modules.commerce.cart.service import CartService

router = APIRouter(prefix="/api/commerce/cart", tags=["commerce.cart"])

_STOREFRONT = "storefront: buyer's own cart (OV-39)"


@router.get("", dependencies=[Depends(authenticated_endpoint(_STOREFRONT))])
async def get_cart(service: CartService = Depends(cart_service)) -> CartDTO:
    return await service.get_cart()


@router.post("/items", dependencies=[Depends(authenticated_endpoint(_STOREFRONT))])
async def add_item(body: AddItemIn, service: CartService = Depends(cart_service)) -> CartDTO:
    return await service.add_item(body.product_id, body.quantity)


@router.delete("/items/{product_id}", dependencies=[Depends(authenticated_endpoint(_STOREFRONT))])
async def remove_item(product_id: UUID, service: CartService = Depends(cart_service)) -> CartDTO:
    return await service.remove_item(product_id)


@router.post("/checkout", dependencies=[Depends(authenticated_endpoint(_STOREFRONT))])
async def checkout(service: CartService = Depends(cart_service)) -> CartDTO:
    return await service.checkout()
