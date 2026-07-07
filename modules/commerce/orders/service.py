"""OrderService — the feature that runs the whole core (interfaces §6.5).

place_order prices items through ProductService (public interface of
commerce.products) and takes payment through PaymentService (public interface of
core/billing) — never touching those tables. The reliable subscriber
(subscribers.py) marks the order paid on ``billing.payment.succeeded`` and sends a
receipt through NotificationService. Buyer methods enforce ownership
(``customer_user_id == ctx.actor.id``); staff read the whole tenant's orders.
"""

from collections.abc import Sequence
from uuid import UUID

from core.billing.service import PaymentService
from modules.commerce.orders.models import Order, OrderItem
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
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._orders = OrderRepository(uow.session, ctx)
        self._items = OrderItemRepository(uow.session, ctx)
        self._products = products  # required by place_order
        self._payments = payments  # required by place_order

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
        return await self._to_dto(order)

    async def list_own(self) -> Sequence[OrderDTO]:
        orders = await self._orders.find(
            Order.customer_user_id == _customer_id(self.ctx), order_by=[Order.created_at.desc()]
        )
        return [await self._to_dto(order) for order in orders]

    # ---------------------------------------------------------------- staff
    async def list_all(self, page: Page) -> PageResult[OrderDTO]:
        result = await self._orders.find_paged(order_by=[Order.created_at.desc()], page=page)
        return PageResult(
            items=[await self._to_dto(order) for order in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
        )

    # ------------------------------------------------- subscriber-driven
    async def mark_paid(self, order_id: UUID, *, payment_id: UUID | None) -> Order | None:
        """Idempotent: returns None for a foreign/missing order, the order otherwise.
        Emits ``commerce.order.paid`` on the transition to paid."""
        order = await self._orders.get(order_id)
        if order is None or order.status == "canceled":
            return None
        if order.status == "paid":
            return order  # already paid — idempotent
        order.status = "paid"
        if payment_id is not None:
            order.payment_id = payment_id
        await self._session.flush()
        self.emit(
            "commerce.order.paid",
            {"order_id": str(order.id), "payment_id": str(payment_id) if payment_id else None},
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
    async def _to_dto(self, order: Order) -> OrderDTO:
        items = await self._items.find(
            OrderItem.order_id == order.id, order_by=[OrderItem.created_at]
        )
        return OrderDTO(
            id=order.id,
            status=order.status,
            total_amount=order.total_amount,
            currency=order.currency,
            payment_id=order.payment_id,
            items=[
                OrderItemDTO(
                    product_id=item.product_id,
                    quantity=item.quantity,
                    unit_price_amount=item.unit_price_amount,
                    currency=item.currency,
                )
                for item in items
            ],
        )
