"""Storefront order endpoints (/api/commerce/orders). Buyer-facing
(authenticated_endpoint + ownership, OV-39); the shop is the X-Shop-Tenant header.
Staff read orders through the admin screen (admin.py), not here.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from core.auth.deps import authenticated_endpoint
from modules.commerce.orders.deps import order_service
from modules.commerce.orders.schemas import OrderCheckoutDTO, OrderDTO, PlaceOrderIn
from modules.commerce.orders.service import OrderService
from shared.pagination import MAX_PAGE_LIMIT, Page, PageResult

router = APIRouter(prefix="/api/commerce/orders", tags=["commerce.orders"])

_STOREFRONT = "storefront: buyer's own orders (OV-39)"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(authenticated_endpoint(_STOREFRONT))],
)
async def place_order(
    body: PlaceOrderIn, service: OrderService = Depends(order_service)
) -> OrderCheckoutDTO:
    return await service.place_order(body.items, body.provider)


@router.get("", dependencies=[Depends(authenticated_endpoint(_STOREFRONT))])
async def list_my_orders(
    service: OrderService = Depends(order_service),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> PageResult[OrderDTO]:
    return await service.list_own(Page(limit=limit, offset=offset))


@router.get("/{order_id}", dependencies=[Depends(authenticated_endpoint(_STOREFRONT))])
async def get_my_order(order_id: UUID, service: OrderService = Depends(order_service)) -> OrderDTO:
    return await service.get_own(order_id)
