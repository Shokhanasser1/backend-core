"""ProductService — the public interface of commerce.products (interfaces §1.2).

Sibling features call ``get_sale_info`` (cart, orders: price a product) and
``ensure_exists`` (product_images: validate a link) without ever touching
commerce_products directly; those two are open to any caller in the tenant
context, storefront buyers included. Staff catalog methods are RBAC-checked here
as well as on the route (PermissionGuard). Events are emitted post-commit.
"""

from uuid import UUID

from core.auth.access_service import AccessService
from core.auth.guard import PermissionGuard
from modules.commerce.products import permissions as perms
from modules.commerce.products.models import Product
from modules.commerce.products.repository import ProductRepository
from modules.commerce.products.schemas import ProductDTO
from shared.context import TenantContext
from shared.errors import ConflictError, InvariantViolationError
from shared.events import EventBus
from shared.money import Money, currency_registry
from shared.pagination import Page, PageResult
from shared.service import Service, UnitOfWork


def _to_dto(product: Product) -> ProductDTO:
    return ProductDTO.model_validate(product)


class ProductService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        access: AccessService | None = None,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._repo = ProductRepository(uow.session, ctx)
        self._guard = PermissionGuard(access, ctx)

    async def create(
        self,
        *,
        sku: str,
        name: str,
        price_amount: int,
        currency: str = "UZS",
        description: str | None = None,
    ) -> ProductDTO:
        await self._guard.require(perms.PRODUCT_CREATE)
        Money(price_amount, currency)  # format + non-negative
        currency_registry.exponent(currency)  # currency must be known (else NotFound)
        if await self._repo.count(Product.sku == sku):
            raise ConflictError(f"product with sku {sku!r} already exists")
        product = Product(
            sku=sku,
            name=name,
            description=description,
            price_amount=price_amount,
            currency=currency,
            status="active",
        )
        await self._repo.add(product)
        self.emit("commerce.product.created", {"product_id": str(product.id)})
        return _to_dto(product)

    async def update(
        self,
        product_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        price_amount: int | None = None,
        currency: str | None = None,
    ) -> ProductDTO:
        await self._guard.require(perms.PRODUCT_UPDATE)
        product = await self._repo.get_or_raise(product_id)
        if product.status == "archived":
            raise InvariantViolationError("cannot update an archived product")
        if name is not None:
            product.name = name
        if description is not None:
            product.description = description
        if price_amount is not None:
            product.price_amount = price_amount
        if currency is not None:
            currency_registry.exponent(currency)
            product.currency = currency
        Money(product.price_amount, product.currency)  # re-validate the resulting price
        await self._session.flush()
        self.emit("commerce.product.updated", {"product_id": str(product.id)})
        return _to_dto(product)

    async def archive(self, product_id: UUID) -> ProductDTO:
        await self._guard.require(perms.PRODUCT_UPDATE)
        product = await self._repo.get_or_raise(product_id)
        if product.status == "archived":
            return _to_dto(product)  # idempotent
        product.status = "archived"
        await self._session.flush()
        self.emit("commerce.product.archived", {"product_id": str(product.id)})
        return _to_dto(product)

    async def get(self, product_id: UUID) -> ProductDTO:
        await self._guard.require(perms.PRODUCT_READ)
        return _to_dto(await self._repo.get_or_raise(product_id))

    async def list(self, page: Page) -> PageResult[ProductDTO]:
        await self._guard.require(perms.PRODUCT_READ)
        result = await self._repo.find_paged(order_by=[Product.created_at.desc()], page=page)
        return PageResult(
            items=[_to_dto(p) for p in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
        )

    async def ensure_exists(self, product_id: UUID) -> None:
        """Public: link validation for sibling features — NotFound for a foreign or
        missing product. Reveals nothing but existence in the context tenant."""
        await self._repo.get_or_raise(product_id)

    async def get_sale_info(self, product_id: UUID) -> Money:
        """Public: price of an active product for sibling features (cart/orders).
        Raises NotFound for a foreign/missing product, InvariantViolation if archived."""
        product = await self._repo.get_or_raise(product_id)
        if product.status != "active":
            raise InvariantViolationError("product is not available for sale")
        return Money(product.price_amount, product.currency)
