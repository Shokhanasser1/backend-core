"""Request-scoped ProductImageService (built on the authenticated ServiceBundle).

Assembles the collaborators it needs from their public interfaces on the SAME
unit of work / tenant context: ProductService (commerce.products) to validate the
product and FileService (core/files) to store the bytes. The bus, storage backend
and settings come from app.state so the feature never imports app.
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle
from core.files import FileService
from modules.commerce.product_images.service import ProductImageService
from modules.commerce.products import ProductService


async def product_image_service(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> ProductImageService:
    state = request.app.state
    products = ProductService(bundle.uow, state.bus, bundle.ctx)
    files = FileService(
        bundle.uow,
        state.bus,
        bundle.ctx,
        storage=state.file_storage,
        thumbnailer=state.file_thumbnailer,
        settings=state.settings,
    )
    return ProductImageService(bundle.uow, state.bus, bundle.ctx, products=products, files=files)
