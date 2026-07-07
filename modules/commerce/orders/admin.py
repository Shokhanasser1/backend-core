"""Orders admin screen (/api/admin/orders): staff view of the tenant's orders.

Gated by ``commerce.order:read`` (owner/admin). Registered on the admin registry
by the feature's ``install()`` (per-app, after admin_registry.reset()). The screen
router carries only require_permission (admin rule §5.4).
"""

from fastapi import APIRouter, Depends, Query

from core.admin.registry import AdminScreen
from core.auth.deps import require_permission
from modules.commerce.orders.deps import staff_order_service
from modules.commerce.orders.permissions import ORDER_READ
from modules.commerce.orders.schemas import OrderDTO
from modules.commerce.orders.service import OrderService
from shared.pagination import MAX_PAGE_LIMIT, Page, PageResult

router = APIRouter()


@router.get("", dependencies=[Depends(require_permission(ORDER_READ))])
async def list_orders(
    service: OrderService = Depends(staff_order_service),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> PageResult[OrderDTO]:
    return await service.list_all(Page(limit=limit, offset=offset))


ORDERS_SCREEN = AdminScreen(
    slug="orders",
    title_key="admin.screen.orders",
    module="commerce.orders",
    router=router,
    permission=ORDER_READ,
)
