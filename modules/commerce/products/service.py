"""ProductService — the public interface of commerce.products (interfaces §1.2).

Sibling features (cart, orders) call ``get_sale_info`` to price a product without
ever touching commerce_products directly. Staff manage the catalog through the
RBAC router. Events are emitted post-commit by the Service base.
"""

from uuid import UUID

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
    def __init__(self, uow: UnitOfWork, bus: EventBus, ctx: TenantContext) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._repo = ProductRepository(uow.session, ctx)

    async def create(
        self,
        *,
        sku: str,
        name: str,
        price_amount: int,
        currency: str = "UZS",
        description: str | None = None,
    ) -> ProductDTO:
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
        product = await self._repo.get_or_raise(product_id)
        if product.status == "archived":
            return _to_dto(product)  # idempotent
        product.status = "archived"
        await self._session.flush()
        self.emit("commerce.product.archived", {"product_id": str(product.id)})
        return _to_dto(product)

    async def get(self, product_id: UUID) -> ProductDTO:
        return _to_dto(await self._repo.get_or_raise(product_id))

    async def list(self, page: Page) -> PageResult[ProductDTO]:
        result = await self._repo.find_paged(order_by=[Product.created_at.desc()], page=page)
        return PageResult(
            items=[_to_dto(p) for p in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
        )

    async def get_sale_info(self, product_id: UUID) -> Money:
        """Public: price of an active product for sibling features (cart/orders).
        Raises NotFound for a foreign/missing product, InvariantViolation if archived."""
        product = await self._repo.get_or_raise(product_id)
        if product.status != "active":
            raise InvariantViolationError("product is not available for sale")
        return Money(product.price_amount, product.currency)
