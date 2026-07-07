"""Staff catalog endpoints (/api/commerce/products). Every route carries one
permission marker (interfaces §5.2). Managed by tenant members via RBAC; the
buyer-facing storefront (cart/orders) prices products through ProductService.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from core.auth.deps import require_permission
from modules.commerce.products import permissions as perms
from modules.commerce.products.deps import product_service
from modules.commerce.products.schemas import CreateProductIn, ProductDTO, UpdateProductIn
from modules.commerce.products.service import ProductService
from shared.pagination import MAX_PAGE_LIMIT, Page, PageResult

router = APIRouter(prefix="/api/commerce/products", tags=["commerce.products"])


@router.get("", dependencies=[Depends(require_permission(perms.PRODUCT_READ))])
async def list_products(
    service: ProductService = Depends(product_service),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> PageResult[ProductDTO]:
    return await service.list(Page(limit=limit, offset=offset))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perms.PRODUCT_CREATE))],
)
async def create_product(
    body: CreateProductIn, service: ProductService = Depends(product_service)
) -> ProductDTO:
    return await service.create(
        sku=body.sku,
        name=body.name,
        price_amount=body.price_amount,
        currency=body.currency,
        description=body.description,
    )


@router.get("/{product_id}", dependencies=[Depends(require_permission(perms.PRODUCT_READ))])
async def get_product(
    product_id: UUID, service: ProductService = Depends(product_service)
) -> ProductDTO:
    return await service.get(product_id)


@router.patch("/{product_id}", dependencies=[Depends(require_permission(perms.PRODUCT_UPDATE))])
async def update_product(
    product_id: UUID, body: UpdateProductIn, service: ProductService = Depends(product_service)
) -> ProductDTO:
    return await service.update(
        product_id,
        name=body.name,
        description=body.description,
        price_amount=body.price_amount,
        currency=body.currency,
    )


@router.post(
    "/{product_id}/archive",
    dependencies=[Depends(require_permission(perms.PRODUCT_UPDATE))],
)
async def archive_product(
    product_id: UUID, service: ProductService = Depends(product_service)
) -> ProductDTO:
    return await service.archive(product_id)
