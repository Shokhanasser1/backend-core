"""commerce.cart hardening (feature test — moves with the feature): checkout turns
the cart into a re-priced order with a payment checkout, the cart closes only on
success, per-line quantity is capped (no DB overflow), a buyer has one active cart
even under concurrent first adds, and every route requires authentication."""

import asyncio
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from modules.commerce.cart.service import CartService
from modules.commerce.products import ProductService
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MIGRATOR
from shared.events import EventBus
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _login, _owner_with_tenant, _register

pytestmark = pytest.mark.integration


def _buyer(client: TestClient, email: str) -> tuple[str, str]:
    user = _register(client, email)
    return cast("str", _login(client, email)["access_token"]), cast("str", user["id"])


def _shop(token: str, shop_id: str) -> dict[str, str]:
    return {**_headers(token), "X-Shop-Tenant": shop_id}


def _product(client: TestClient, owner: str, *, sku: str, price: int) -> str:
    resp = client.post(
        "/api/commerce/products",
        headers=_headers(owner),
        json={"sku": sku, "name": "Tea", "price_amount": price},
    )
    assert resp.status_code == 201, resp.text
    return cast("str", resp.json()["id"])


def _add(client: TestClient, buyer: str, shop_id: str, pid: str, qty: int) -> Any:
    return client.post(
        "/api/commerce/cart/items",
        headers=_shop(buyer, shop_id),
        json={"product_id": pid, "quantity": qty},
    )


def test_checkout_creates_a_repriced_order_with_payment(
    commerce_payments_client: TestClient,
) -> None:
    client = commerce_payments_client
    _u, shop_id, owner = _owner_with_tenant(client, "chk@example.uz")
    pid = _product(client, owner, sku="C1", price=5000)
    buyer, _ = _buyer(client, "cbuyer@example.uz")
    _add(client, buyer, shop_id, pid, 3)
    # The price changes after the item went into the cart: checkout re-prices.
    client.patch(
        f"/api/commerce/products/{pid}", headers=_headers(owner), json={"price_amount": 6000}
    )

    resp = client.post(
        "/api/commerce/cart/checkout", headers=_shop(buyer, shop_id), json={"provider": "payme"}
    )

    assert resp.status_code == 201, resp.text
    checkout = resp.json()
    assert checkout["provider"] == "payme"
    assert checkout["checkout_url"]
    order = client.get(
        f"/api/commerce/orders/{checkout['order_id']}", headers=_shop(buyer, shop_id)
    ).json()
    assert order["status"] == "pending"
    assert order["total_amount"] == 18000
    assert order["items"][0]["quantity"] == 3
    # The cart is closed: the next add starts a fresh one.
    assert client.get("/api/commerce/cart", headers=_shop(buyer, shop_id)).status_code == 404


@pytest.mark.parametrize("provider", ["nope"])
def test_failed_checkout_keeps_the_cart_open(
    commerce_payments_client: TestClient, provider: str
) -> None:
    client = commerce_payments_client
    _u, shop_id, owner = _owner_with_tenant(client, "kep@example.uz")
    pid = _product(client, owner, sku="C2", price=100)
    buyer, _ = _buyer(client, "kbuyer@example.uz")
    _add(client, buyer, shop_id, pid, 1)

    resp = client.post(
        "/api/commerce/cart/checkout", headers=_shop(buyer, shop_id), json={"provider": provider}
    )

    assert resp.status_code == 404  # unknown payment provider
    cart = client.get("/api/commerce/cart", headers=_shop(buyer, shop_id))
    assert cart.status_code == 200
    assert cart.json()["status"] == "active"


def test_line_quantity_is_capped_not_overflowed(commerce_client: TestClient) -> None:
    _u, shop_id, owner = _owner_with_tenant(commerce_client, "cap@example.uz")
    pid = _product(commerce_client, owner, sku="C3", price=1)
    buyer, _ = _buyer(commerce_client, "capbuyer@example.uz")
    assert _add(commerce_client, buyer, shop_id, pid, 1000).status_code == 200

    over = _add(commerce_client, buyer, shop_id, pid, 1)

    assert over.status_code == 422
    cart = commerce_client.get("/api/commerce/cart", headers=_shop(buyer, shop_id)).json()
    assert cart["items"][0]["quantity"] == 1000


def test_concurrent_first_adds_open_one_cart(
    commerce_client: TestClient, role_urls: dict[str, str]
) -> None:
    _u, shop_id, owner = _owner_with_tenant(commerce_client, "one@example.uz")
    pid = _product(commerce_client, owner, sku="C4", price=10)
    _token, buyer_id = _buyer(commerce_client, "onebuyer@example.uz")
    app = cast("FastAPI", commerce_client.app)
    ctx = TenantContext(
        tenant_id=UUID(shop_id), actor=Actor(kind="user", id=buyer_id), request_id=None
    )

    async def race() -> int:
        engine = create_async_engine(app.state.settings.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def add() -> None:
            async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                bus = EventBus()
                products = ProductService(uow, bus, ctx)
                await CartService(uow, bus, ctx, products=products).add_item(UUID(pid), 1)

        try:
            await asyncio.gather(add(), add())
        finally:
            await engine.dispose()
        owner_engine = create_async_engine(role_urls[ROLE_MIGRATOR])
        try:
            async with owner_engine.connect() as conn:
                return int(
                    (
                        await conn.execute(
                            text(
                                "SELECT count(*) FROM commerce_carts "
                                "WHERE customer_user_id = :c AND status = 'active'"
                            ),
                            {"c": UUID(buyer_id)},
                        )
                    ).scalar_one()
                )
        finally:
            await owner_engine.dispose()

    assert asyncio.run(race()) == 1
    cart = commerce_client.get("/api/commerce/cart", headers=_shop(_token, shop_id)).json()
    assert cart["items"][0]["quantity"] == 2  # both adds landed in the one cart


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/commerce/cart"),
        ("POST", "/api/commerce/cart/items"),
        ("DELETE", "/api/commerce/cart/items/00000000-0000-0000-0000-000000000000"),
        ("POST", "/api/commerce/cart/checkout"),
    ],
)
def test_cart_requires_authentication(commerce_client: TestClient, method: str, path: str) -> None:
    resp = commerce_client.request(method, path, headers={"X-Shop-Tenant": str(UUID(int=1))})
    assert resp.status_code == 401
