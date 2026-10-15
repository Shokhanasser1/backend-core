"""OrderService — the feature that runs the whole core (interfaces §6.5).

place_order prices items through ProductService (public interface of
commerce.products) and takes payment through PaymentService (public interface of
core/billing) — never touching those tables. The reliable subscriber
(subscribers.py) marks the order paid on ``billing.payment.succeeded`` and sends a
receipt through NotificationService. Buyer methods enforce ownership
(``customer_user_id == ctx.actor.id``); staff read the whole tenant's orders —
RBAC-checked here as well as on the route (PermissionGuard).

An order canceled because its checkout EXPIRED is revived to paid when that same
payment succeeds late (``late`` flag from billing): money has moved, there is no
stock reservation to restore, and the lines are a price snapshot.
"""

from collections.abc import Sequence
from uuid import UUID

from core.auth.access_service import AccessService
from core.auth.guard import PermissionGuard
from core.billing.service import PaymentService
from modules.commerce.orders.models import Order, OrderItem
from modules.commerce.orders.permissions import ORDER_READ
from modules.commerce.orders.repository import OrderItemRepository, OrderRepository
from modules.commerce.orders.schemas import OrderCheckoutDTO, OrderDTO, OrderItemDTO, OrderLineIn
from modules.commerce.products import ProductService
from shared.context import TenantContext
from shared.errors import AuthenticationError, InvariantViolationError, NotFoundError
from shared.events import EventBus
from shared.money import Money
from shared.pagination import Page, PageResult
from shared.service import Service, UnitOfWork


def _customer_id(ctx: TenantContext) -> UUID:
    if ctx.actor.kind != "user" or ctx.actor.id is None:
        raise AuthenticationError("storefront requires an authenticated buyer")
    return UUID(ctx.actor.id)


class OrderService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        products: ProductService | None = None,
        payments: PaymentService | None = None,
        access: AccessService | None = None,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._orders = OrderRepository(uow.session, ctx)
        self._items = OrderItemRepository(uow.session, ctx)
        self._products = products  # required by place_order
        self._payments = payments  # required by place_order
        self._guard = PermissionGuard(access, ctx)

    # ---------------------------------------------------------------- buyer
    async def place_order(self, lines: Sequence[OrderLineIn], provider: str) -> OrderCheckoutDTO:
        if self._products is None or self._payments is None:
            raise InvariantViolationError("place_order requires products and payments services")
        customer = _customer_id(self.ctx)

        priced: list[tuple[UUID, int, int, str]] = []
        total = 0
        currency = ""
        for line in lines:
            price = await self._products.get_sale_info(line.product_id)  # NotFound if unavailable
            if currency == "":
                currency = price.currency
            elif currency != price.currency:
                raise InvariantViolationError("order items must share one currency")
            priced.append((line.product_id, line.quantity, price.amount, price.currency))
            total += price.amount * line.quantity
        if currency == "":
            raise InvariantViolationError("order must contain at least one item")

        order = await self._orders.add(
            Order(
                customer_user_id=customer,
                status="pending",
                total_amount=total,
                currency=currency,
            )
        )
        for product_id, quantity, unit_price, item_currency in priced:
            await self._items.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product_id,
                    quantity=quantity,
                    unit_price_amount=unit_price,
                    currency=item_currency,
                )
            )
        self.emit(
            "commerce.order.created",
            {
                "order_id": str(order.id),
                "customer_user_id": str(customer),
                "total_amount": total,
                "currency": currency,
            },
        )
        # Take payment through billing's public interface; reference ties the
        # payment back to this order for the paid/failed subscribers.
        checkout = await self._payments.create_payment(
            Money(total, currency),
            purpose="commerce.order",
            reference=str(order.id),
            provider=provider,
            idempotency_key=f"order:{order.id}",
        )
        order.payment_id = checkout.payment_id
        await self._session.flush()
        return OrderCheckoutDTO(
            order_id=order.id,
            payment_id=checkout.payment_id,
            provider=checkout.provider,
            checkout_url=checkout.checkout_url,
        )

    async def get_own(self, order_id: UUID) -> OrderDTO:
        order = await self._orders.get_or_raise(order_id)
        if order.customer_user_id != _customer_id(self.ctx):
            raise NotFoundError(f"Order {order_id} not found")  # foreign == missing (V1)
        return (await self._to_dtos([order]))[0]

    async def list_own(self, page: Page) -> PageResult[OrderDTO]:
        result = await self._orders.find_paged(
            Order.customer_user_id == _customer_id(self.ctx),
            order_by=[Order.created_at.desc()],
            page=page,
        )
        return await self._page(result)

    # ---------------------------------------------------------------- staff
    async def list_all(self, page: Page) -> PageResult[OrderDTO]:
        await self._guard.require(ORDER_READ)
        result = await self._orders.find_paged(order_by=[Order.created_at.desc()], page=page)
        return await self._page(result)

    # ------------------------------------------------- subscriber-driven
    async def mark_paid(
        self, order_id: UUID, *, payment_id: UUID | None, late: bool = False
    ) -> Order | None:
        """Idempotent: returns None for a foreign/missing order (or a canceled one
        that this payment cannot revive), the order otherwise. A late success of
        the order's OWN payment revives an order canceled by checkout expiry.
        Emits ``commerce.order.paid`` on the transition to paid."""
        order = await self._orders.get(order_id)
        if order is None:
            return None
        if order.status == "paid":
            return order  # already paid — idempotent
        revived = order.status == "canceled"
        if revived and not (late and payment_id is not None and order.payment_id == payment_id):
            return None
        order.status = "paid"
        if payment_id is not None:
            order.payment_id = payment_id
        await self._session.flush()
        self.emit(
            "commerce.order.paid",
            {
                "order_id": str(order.id),
                "payment_id": str(payment_id) if payment_id else None,
                "late": revived,
            },
        )
        return order

    async def cancel(self, order_id: UUID, *, reason: str) -> None:
        order = await self._orders.get(order_id)
        if order is None or order.status in ("paid", "canceled"):
            return  # a paid order is not canceled by a late failure (idempotent)
        order.status = "canceled"
        await self._session.flush()
        self.emit("commerce.order.canceled", {"order_id": str(order.id), "reason": reason})

    # ---------------------------------------------------------------- shared
    async def _page(self, result: PageResult[Order]) -> PageResult[OrderDTO]:
        return PageResult(
            items=await self._to_dtos(result.items),
            total=result.total,
            limit=result.limit,
            offset=result.offset,
        )

    async def _to_dtos(self, orders: Sequence[Order]) -> list[OrderDTO]:
        """All the orders' lines in ONE query (no per-order round trip)."""
        lines: dict[UUID, list[OrderItemDTO]] = {order.id: [] for order in orders}
        if lines:
            for item in await self._items.find(
                OrderItem.order_id.in_(list(lines)), order_by=[OrderItem.created_at]
            ):
                lines[item.order_id].append(
                    OrderItemDTO(
                        product_id=item.product_id,
                        quantity=item.quantity,
                        unit_price_amount=item.unit_price_amount,
                        currency=item.currency,
                    )
                )
        return [
            OrderDTO(
                id=order.id,
                status=order.status,
                total_amount=order.total_amount,
                currency=order.currency,
                payment_id=order.payment_id,
                items=lines[order.id],
            )
            for order in orders
        ]
