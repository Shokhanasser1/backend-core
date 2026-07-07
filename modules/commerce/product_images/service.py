"""ProductImageService — attach / list / serve / detach images for a product.

Staff-managed (RBAC). The product is validated through ``ProductService`` and the
bytes go through ``FileService`` (both public interfaces of their components);
this feature never reads commerce_products or the files table directly. Events fire
post-commit.
"""

from collections.abc import Sequence
from uuid import UUID

from core.files import FileDTO, FileService
from modules.commerce.product_images.models import ProductImage
from modules.commerce.product_images.repository import ProductImageRepository
from modules.commerce.product_images.schemas import ProductImageDTO
from modules.commerce.products import ProductService
from shared.context import TenantContext
from shared.events import EventBus
from shared.service import Service, UnitOfWork


def _to_dto(image: ProductImage) -> ProductImageDTO:
    return ProductImageDTO.model_validate(image)


class ProductImageService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        products: ProductService,
        files: FileService,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._repo = ProductImageRepository(uow.session, ctx)
        self._products = products
        self._files = files

    @property
    def max_upload_bytes(self) -> int:
        return self._files.max_upload_bytes

    async def attach(
        self,
        *,
        product_id: UUID,
        filename: str | None,
        declared_content_type: str | None,
        data: bytes,
        alt_text: str | None = None,
        position: int = 0,
    ) -> ProductImageDTO:
        # Validate the product via the sibling feature's public interface (NotFound
        # for a foreign/missing product) BEFORE storing any bytes.
        await self._products.get(product_id)
        stored = await self._files.upload(
            filename=filename, declared_content_type=declared_content_type, data=data
        )
        image = ProductImage(
            product_id=product_id, file_id=stored.id, position=position, alt_text=alt_text
        )
        await self._repo.add(image)
        self.emit(
            "commerce.product_image.added",
            {
                "image_id": str(image.id),
                "product_id": str(product_id),
                "file_id": str(stored.id),
            },
        )
        return _to_dto(image)

    async def list_for_product(self, product_id: UUID) -> Sequence[ProductImageDTO]:
        images = await self._repo.find(
            ProductImage.product_id == product_id,
            order_by=[ProductImage.position, ProductImage.created_at],
        )
        return [_to_dto(image) for image in images]

    async def open_content(self, image_id: UUID) -> tuple[FileDTO, bytes]:
        """The image bytes for an owned image (404 for a foreign/missing one)."""
        image = await self._repo.get_or_raise(image_id)
        return await self._files.open(image.file_id)

    async def remove(self, image_id: UUID) -> None:
        image = await self._repo.get_or_raise(image_id)
        product_id, file_id = image.product_id, image.file_id
        # Drop the link row first, then the file (its storage delete runs last, so a
        # backend failure rolls everything back).
        await self._repo.delete(image)
        await self._session.flush()
        await self._files.delete(file_id)
        self.emit(
            "commerce.product_image.removed",
            {"image_id": str(image_id), "product_id": str(product_id), "file_id": str(file_id)},
        )
