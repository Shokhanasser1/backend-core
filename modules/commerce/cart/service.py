"""CartService — buyer's cart (storefront, decision OV-39).

Ownership is enforced by construction: every query is scoped to the current
buyer (``customer_user_id == ctx.actor.id``), so a buyer can only ever touch
their own cart. Prices come from ``ProductService.get_sale_info`` (the public
interface of commerce.products) — the cart never reads the products table.

A buyer has ONE active cart per shop (partial unique index): a concurrent first
add that loses the race re-reads the winner's cart, and every change locks the
cart row, so concurrent adds of the same product merge instead of colliding.
Checkout hands the lines to ``OrderService.place_order`` (public interface of
commerce.orders), which re-prices them at the CURRENT price and opens the payment;
the cart closes only if that succeeds — any failure rolls the request back and the
cart stays as it was.
"""

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from modules.commerce.cart.models import Cart, CartItem
from modules.commerce.cart.repository import CartItemRepository, CartRepository
from modules.commerce.cart.schemas import CartDTO, CartItemDTO
from modules.commerce.orders import OrderCheckoutDTO, OrderLineIn, OrderService
from modules.commerce.products import ProductService
from shared.context import TenantContext
from shared.errors import AuthenticationError, InvariantViolationError, NotFoundError
from shared.events import EventBus
from shared.service import Service, UnitOfWork

# Per-line cap: matches one request's limit (AddItemIn) and keeps the stored
# quantity far inside its SmallInteger column.
MAX_LINE_QUANTITY = 1000


def _customer_id(ctx: TenantContext) -> UUID:
    if ctx.actor.kind != "user" or ctx.actor.id is None:
        raise AuthenticationError("storefront requires an authenticated buyer")
    return UUID(ctx.actor.id)


class CartService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        products: ProductService,
        orders: OrderService | None = None,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._carts = CartRepository(uow.session, ctx)
        self._items = CartItemRepository(uow.session, ctx)
        self._products = products
        self._orders = orders  # required by checkout
        self._customer = _customer_id(ctx)

    async def _active_cart(self, *, lock: bool = False) -> Cart | None:
        found = await self._carts.find(
            Cart.customer_user_id == self._customer, Cart.status == "active", for_update=lock
        )
        return found[0] if found else None

    async def _open_cart(self) -> Cart:
        """The buyer's active cart, created if absent, locked for this transaction."""
        cart = await self._active_cart(lock=True)
        if cart is not None:
            return cart
        try:
            async with self._session.begin_nested():
                await self._carts.add(Cart(customer_user_id=self._customer, status="active"))
        except IntegrityError:
            pass  # a concurrent first add opened it (our INSERT waited for its commit)
        cart = await self._active_cart(lock=True)
        if cart is None:  # pragma: no cover - the unique index guarantees one exists
            raise InvariantViolationError("could not open a cart")
        return cart

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
        total_quantity = quantity + (existing[0].quantity if existing else 0)
        if total_quantity > MAX_LINE_QUANTITY:
            raise InvariantViolationError(f"at most {MAX_LINE_QUANTITY} of one product per cart")
        if existing:
            item = existing[0]
            item.quantity = total_quantity
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
        cart = await self._require_active_cart(lock=True)
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

    async def checkout(self, provider: str) -> OrderCheckoutDTO:
        """Turn the cart into an order + payment checkout; the cart closes only
        if the order was placed (otherwise the request rolls back, cart intact)."""
        if self._orders is None:
            raise InvariantViolationError("checkout requires the orders service")
        cart = await self._require_active_cart(lock=True)
        items = await self._items.find(CartItem.cart_id == cart.id, order_by=[CartItem.created_at])
        if not items:
            raise InvariantViolationError("cannot checkout an empty cart")
        checkout = await self._orders.place_order(
            [OrderLineIn(product_id=item.product_id, quantity=item.quantity) for item in items],
            provider,
        )
        cart.status = "checked_out"
        await self._session.flush()
        self.emit(
            "commerce.cart.checked_out",
            {
                "cart_id": str(cart.id),
                "customer_user_id": str(self._customer),
                "order_id": str(checkout.order_id),
            },
        )
        return checkout

    async def _require_active_cart(self, *, lock: bool = False) -> Cart:
        cart = await self._active_cart(lock=lock)
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
