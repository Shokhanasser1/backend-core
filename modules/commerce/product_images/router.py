"""Staff endpoints (/api/commerce/product-images). Every route carries one
permission marker (§5.2). Images are attached to a product by tenant members via
RBAC; the bytes are served inline (only allowlisted raster images are stored and
the global security headers add nosniff, so inline is XSS-safe).
"""

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import Response

from core.auth.deps import require_permission
from modules.commerce.product_images import permissions as perms
from modules.commerce.product_images.deps import product_image_service
from modules.commerce.product_images.schemas import ProductImageDTO
from modules.commerce.product_images.service import ProductImageService

router = APIRouter(prefix="/api/commerce/product-images", tags=["commerce.product_images"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perms.PRODUCT_IMAGE_MANAGE))],
)
async def attach_image(
    product_id: UUID = Form(...),
    upload: UploadFile = File(...),
    alt_text: str | None = Form(default=None),
    position: int = Form(default=0),
    service: ProductImageService = Depends(product_image_service),
) -> ProductImageDTO:
    data = await upload.read(service.max_upload_bytes + 1)
    return await service.attach(
        product_id=product_id,
        filename=upload.filename,
        declared_content_type=upload.content_type,
        data=data,
        alt_text=alt_text,
        position=position,
    )


@router.get("", dependencies=[Depends(require_permission(perms.PRODUCT_IMAGE_READ))])
async def list_images(
    product_id: UUID = Query(...),
    service: ProductImageService = Depends(product_image_service),
) -> Sequence[ProductImageDTO]:
    return await service.list_for_product(product_id)


@router.get(
    "/{image_id}/content",
    dependencies=[Depends(require_permission(perms.PRODUCT_IMAGE_READ))],
)
async def get_image_content(
    image_id: UUID,
    size: Literal["original", "thumb"] = Query(default="original"),
    service: ProductImageService = Depends(product_image_service),
) -> Response:
    dto, data = await service.open_content(image_id, variant=size)
    return Response(
        content=data, media_type=dto.content_type, headers={"Content-Disposition": "inline"}
    )


@router.delete(
    "/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perms.PRODUCT_IMAGE_MANAGE))],
)
async def remove_image(
    image_id: UUID, service: ProductImageService = Depends(product_image_service)
) -> None:
    await service.remove(image_id)
