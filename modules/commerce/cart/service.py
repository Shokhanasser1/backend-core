"""CartService — buyer's cart (storefront, decision OV-39).

Ownership is enforced by construction: every query is scoped to the current
buyer (``customer_user_id == ctx.actor.id``), so a buyer can only ever touch
their own cart. Prices come from ``ProductService.get_sale_info`` (the public
interface of commerce.products) — the cart never reads the products table.
"""

from uuid import UUID

from modules.commerce.cart.models import Cart, CartItem
from modules.commerce.cart.repository import CartItemRepository, CartRepository
from modules.commerce.cart.schemas import CartDTO, CartItemDTO
from modules.commerce.products import ProductService
from shared.context import TenantContext
from shared.errors import AuthenticationError, InvariantViolationError, NotFoundError
from shared.events import EventBus
from shared.service import Service, UnitOfWork


def _customer_id(ctx: TenantContext) -> UUID:
    if ctx.actor.kind != "user" or ctx.actor.id is None:
        raise AuthenticationError("storefront requires an authenticated buyer")
    return UUID(ctx.actor.id)


class CartService(Service):
    def __init__(
        self, uow: UnitOfWork, bus: EventBus, ctx: TenantContext, *, products: ProductService
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._carts = CartRepository(uow.session, ctx)
        self._items = CartItemRepository(uow.session, ctx)
        self._products = products
        self._customer = _customer_id(ctx)

    async def _active_cart(self) -> Cart | None:
        found = await self._carts.find(
            Cart.customer_user_id == self._customer, Cart.status == "active"
        )
        return found[0] if found else None

    async def _open_cart(self) -> Cart:
        """The buyer's active cart, created if absent."""
        cart = await self._active_cart()
        if cart is not None:
            return cart
        return await self._carts.add(Cart(customer_user_id=self._customer, status="active"))

    async def add_item(self, product_id: UUID, quantity: int) -> CartDTO:
        # Price + availability from the sibling feature's public interface (NotFound
        # for a foreign/missing product, InvariantViolation if archived).
        price = await self._products.get_sale_info(product_id)
        cart = await self._open_cart()
        existing = await self._items.find(
            CartItem.cart_id == cart.id, CartItem.product_id == product_id
        )
        current_items = await self._items.find(CartItem.cart_id == cart.id)
        if current_items and current_items[0].currency != price.currency:
            raise InvariantViolationError("cart items must share one currency")
        if existing:
            item = existing[0]
            item.quantity += quantity
            item.unit_price_amount = price.amount  # refresh to the current price
            item.currency = price.currency
            await self._session.flush()
        else:
            await self._items.add(
                CartItem(
                    cart_id=cart.id,
                    product_id=product_id,
                    quantity=quantity,
                    unit_price_amount=price.amount,
                    currency=price.currency,
                )
            )
        return await self._to_dto(cart)

    async def remove_item(self, product_id: UUID) -> CartDTO:
        cart = await self._require_active_cart()
        items = await self._items.find(
            CartItem.cart_id == cart.id, CartItem.product_id == product_id
        )
        if not items:
            raise NotFoundError("product is not in the cart")
        await self._items.delete(items[0])
        await self._session.flush()
        return await self._to_dto(cart)

    async def get_cart(self) -> CartDTO:
        return await self._to_dto(await self._require_active_cart())

    async def checkout(self) -> CartDTO:
        cart = await self._require_active_cart()
        items = await self._items.find(CartItem.cart_id == cart.id)
        if not items:
            raise InvariantViolationError("cannot checkout an empty cart")
        total = sum(item.unit_price_amount * item.quantity for item in items)
        cart.status = "checked_out"
        await self._session.flush()
        self.emit(
            "commerce.cart.checked_out",
            {
                "cart_id": str(cart.id),
                "customer_user_id": str(self._customer),
                "total_amount": total,
                "currency": items[0].currency,
            },
        )
        return await self._to_dto(cart)

    async def _require_active_cart(self) -> Cart:
        cart = await self._active_cart()
        if cart is None:
            raise NotFoundError("no active cart")
        return cart

    async def _to_dto(self, cart: Cart) -> CartDTO:
        items = await self._items.find(CartItem.cart_id == cart.id, order_by=[CartItem.created_at])
        total = sum(item.unit_price_amount * item.quantity for item in items)
        currency = items[0].currency if items else "UZS"
        return CartDTO(
            id=cart.id,
            status=cart.status,
            currency=currency,
            total_amount=total,
            items=[
                CartItemDTO(
                    product_id=item.product_id,
                    quantity=item.quantity,
                    unit_price_amount=item.unit_price_amount,
                    currency=item.currency,
                )
                for item in items
            ],
        )
