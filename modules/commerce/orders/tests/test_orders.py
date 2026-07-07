"""commerce.orders end-to-end (feature test — the Phase 6 §6.5 scenario).

Buyer places an order (priced via products, paid via billing) → pending + checkout;
a simulated ``billing.payment.succeeded`` drives the reliable subscriber, which
marks the order paid and queues a receipt for the buyer. Also covers buyer
ownership isolation and the staff admin screen (owner reads, member 403).
"""

import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.notifications.models import NotificationOutbox
from modules.commerce.orders.subscribers import cancel_order_on_failure, mark_order_paid
from shared.context import Actor, TenantContext
from shared.events import EventEnvelope, bus
from shared.handler_runtime import HandlerRuntime, reset_handler_runtime, set_handler_runtime
from shared.ids import new_uuid7
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _login, _owner_with_tenant, _register
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


def _buyer_token(client: TestClient, email: str) -> str:
    _register(client, email)
    return cast("str", _login(client, email)["access_token"])


def _shop_headers(token: str, shop_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Shop-Tenant": shop_id}


def _new_product(client: TestClient, owner: str, *, sku: str, price: int) -> str:
    resp = client.post(
        "/api/commerce/products",
        headers=_headers(owner),
        json={"sku": sku, "name": "Item", "price_amount": price},
    )
    assert resp.status_code == 201, resp.text
    return cast("str", resp.json()["id"])


def test_place_order_creates_pending_with_checkout(
    commerce_payments_client: TestClient,
) -> None:
    client = commerce_payments_client
    _u, shop_id, owner = _owner_with_tenant(client, "sho@example.uz")
    pid = _new_product(client, owner, sku="O1", price=12000)
    buyer = _buyer_token(client, "buyerx@example.uz")

    placed = client.post(
        "/api/commerce/orders",
        headers=_shop_headers(buyer, shop_id),
        json={"provider": "payme", "items": [{"product_id": pid, "quantity": 2}]},
    )
    assert placed.status_code == 201, placed.text
    checkout = placed.json()
    assert checkout["provider"] == "payme"
    assert checkout["checkout_url"].startswith("https://checkout.paycom.uz/")

    orders = client.get("/api/commerce/orders", headers=_shop_headers(buyer, shop_id)).json()
    assert len(orders) == 1
    assert orders[0]["status"] == "pending"
    assert orders[0]["total_amount"] == 24000


def test_orders_are_isolated_per_buyer(commerce_payments_client: TestClient) -> None:
    client = commerce_payments_client
    _u, shop_id, owner = _owner_with_tenant(client, "iso@example.uz")
    pid = _new_product(client, owner, sku="O2", price=100)
    b1 = _buyer_token(client, "buyerone@example.uz")
    b2 = _buyer_token(client, "buyertwo@example.uz")

    order_id = client.post(
        "/api/commerce/orders",
        headers=_shop_headers(b1, shop_id),
        json={"provider": "payme", "items": [{"product_id": pid, "quantity": 1}]},
    ).json()["order_id"]

    # b2 cannot see b1's order (foreign == 404).
    assert (
        client.get(
            f"/api/commerce/orders/{order_id}", headers=_shop_headers(b2, shop_id)
        ).status_code
        == 404
    )
    assert client.get("/api/commerce/orders", headers=_shop_headers(b2, shop_id)).json() == []


def test_staff_admin_lists_orders_member_forbidden(
    commerce_payments_client: TestClient,
) -> None:
    client = commerce_payments_client
    _u, shop_id, owner = _owner_with_tenant(client, "stf@example.uz")
    pid = _new_product(client, owner, sku="O3", price=500)
    member = _member_of(client, owner, shop_id, "clerk@example.uz")
    buyer = _buyer_token(client, "buyerz@example.uz")
    client.post(
        "/api/commerce/orders",
        headers=_shop_headers(buyer, shop_id),
        json={"provider": "payme", "items": [{"product_id": pid, "quantity": 1}]},
    )

    # Owner has commerce.order:read -> the admin screen lists the tenant's orders.
    listed = client.get("/api/admin/orders", headers=_headers(owner))
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    # The screen is in the owner's admin menu.
    menu = client.get("/api/admin/screens", headers=_headers(owner)).json()
    assert "orders" in {s["slug"] for s in menu}
    # A member lacks commerce.order:read -> 403 (DoD negative test).
    assert client.get("/api/admin/orders", headers=_headers(member)).status_code == 403


def _run_subscriber(
    subscriber: object,
    session_factory: async_sessionmaker[AsyncSession],
    shop_id: str,
    event_name: str,
    order_id: str,
    amount: int,
) -> None:
    ctx = TenantContext(
        tenant_id=UUID(shop_id), actor=Actor(kind="integration", id="payme"), request_id=None
    )
    envelope = EventEnvelope(
        event_id=new_uuid7(),
        name=event_name,
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=UUID(shop_id),
        actor=ctx.actor,
        payload={
            "purpose": "commerce.order",
            "reference": order_id,
            "payment_id": str(uuid4()),
            "amount": amount,
            "currency": "UZS",
            "provider": "payme",
        },
    )

    async def run() -> None:
        async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
            token = set_handler_runtime(HandlerRuntime(uow=uow, ctx=ctx, bus=bus))
            try:
                await subscriber(envelope)  # type: ignore[operator]
            finally:
                reset_handler_runtime(token)

    asyncio.run(run())


def test_paid_subscriber_marks_order_and_queues_receipt(
    commerce_payments_client: TestClient,
    session_factory: async_sessionmaker[AsyncSession],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = commerce_payments_client
    _u, shop_id, owner = _owner_with_tenant(client, "pay@example.uz")
    pid = _new_product(client, owner, sku="O4", price=7000)
    buyer = _buyer_token(client, "payer@example.uz")
    order_id = client.post(
        "/api/commerce/orders",
        headers=_shop_headers(buyer, shop_id),
        json={"provider": "payme", "items": [{"product_id": pid, "quantity": 1}]},
    ).json()["order_id"]

    # Simulate billing.payment.succeeded reaching the reliable subscriber.
    _run_subscriber(
        mark_order_paid, session_factory, shop_id, "billing.payment.succeeded", order_id, 7000
    )

    paid = client.get(f"/api/commerce/orders/{order_id}", headers=_shop_headers(buyer, shop_id))
    assert paid.json()["status"] == "paid"

    async def _count_receipts() -> int:
        async with maintenance_session_factory() as session:
            return (
                await session.execute(
                    select(func.count())
                    .select_from(NotificationOutbox)
                    .where(NotificationOutbox.template_key == "commerce.order_paid")
                )
            ).scalar_one()

    assert asyncio.run(_count_receipts()) >= 1


def test_failed_payment_cancels_order(
    commerce_payments_client: TestClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = commerce_payments_client
    _u, shop_id, owner = _owner_with_tenant(client, "fai@example.uz")
    pid = _new_product(client, owner, sku="O5", price=3000)
    buyer = _buyer_token(client, "failbuyer@example.uz")
    order_id = client.post(
        "/api/commerce/orders",
        headers=_shop_headers(buyer, shop_id),
        json={"provider": "payme", "items": [{"product_id": pid, "quantity": 1}]},
    ).json()["order_id"]

    _run_subscriber(
        cancel_order_on_failure, session_factory, shop_id, "billing.payment.failed", order_id, 3000
    )

    got = client.get(f"/api/commerce/orders/{order_id}", headers=_shop_headers(buyer, shop_id))
    assert got.json()["status"] == "canceled"
