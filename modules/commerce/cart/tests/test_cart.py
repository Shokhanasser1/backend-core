"""commerce.cart storefront flow (feature test — moves with the feature).

Buyer flow (OV-39): a shop owner (tenant member) creates a product; a buyer — an
authenticated user who is NOT a member — browses that shop via the X-Shop-Tenant
header, fills a cart and checks out. Exercises ownership isolation between buyers
and the missing-shop-header error.
"""

from typing import cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.test_auth_flows import _headers, _login, _owner_with_tenant, _register

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
        json={"sku": sku, "name": "Tea", "price_amount": price},
    )
    assert resp.status_code == 201, resp.text
    return cast("str", resp.json()["id"])


def test_buyer_fills_cart_and_checks_out(commerce_client: TestClient) -> None:
    _u, shop_id, owner = _owner_with_tenant(commerce_client, "sho@example.uz")
    pid = _new_product(commerce_client, owner, sku="P1", price=5000)

    buyer = _buyer_token(commerce_client, "buyerx@example.uz")
    added = commerce_client.post(
        "/api/commerce/cart/items",
        headers=_shop_headers(buyer, shop_id),
        json={"product_id": pid, "quantity": 3},
    )
    assert added.status_code == 200, added.text
    cart = added.json()
    assert cart["total_amount"] == 15000
    assert cart["items"][0]["quantity"] == 3

    checked_out = commerce_client.post(
        "/api/commerce/cart/checkout", headers=_shop_headers(buyer, shop_id)
    )
    assert checked_out.status_code == 200
    assert checked_out.json()["status"] == "checked_out"


def test_remove_item_empties_cart(commerce_client: TestClient) -> None:
    _u, shop_id, owner = _owner_with_tenant(commerce_client, "rem@example.uz")
    pid = _new_product(commerce_client, owner, sku="P3", price=200)
    buyer = _buyer_token(commerce_client, "remover@example.uz")
    commerce_client.post(
        "/api/commerce/cart/items",
        headers=_shop_headers(buyer, shop_id),
        json={"product_id": pid, "quantity": 2},
    )
    removed = commerce_client.request(
        "DELETE", f"/api/commerce/cart/items/{pid}", headers=_shop_headers(buyer, shop_id)
    )
    assert removed.status_code == 200
    assert removed.json()["items"] == []
    assert removed.json()["total_amount"] == 0


def test_missing_shop_header_is_rejected(commerce_client: TestClient) -> None:
    buyer = _buyer_token(commerce_client, "noshop@example.uz")
    # No X-Shop-Tenant -> the storefront context cannot resolve a shop (422).
    assert commerce_client.get("/api/commerce/cart", headers=_headers(buyer)).status_code == 422


def test_carts_are_isolated_per_buyer(commerce_client: TestClient) -> None:
    _u, shop_id, owner = _owner_with_tenant(commerce_client, "own@example.uz")
    pid = _new_product(commerce_client, owner, sku="P2", price=100)

    b1 = _buyer_token(commerce_client, "buyerone@example.uz")
    b2 = _buyer_token(commerce_client, "buyertwo@example.uz")
    commerce_client.post(
        "/api/commerce/cart/items",
        headers=_shop_headers(b1, shop_id),
        json={"product_id": pid, "quantity": 1},
    )

    # b2 has no cart of their own.
    assert (
        commerce_client.get("/api/commerce/cart", headers=_shop_headers(b2, shop_id)).status_code
        == 404
    )
    # b1 sees only their cart.
    mine = commerce_client.get("/api/commerce/cart", headers=_shop_headers(b1, shop_id))
    assert mine.json()["items"][0]["product_id"] == pid


def test_add_unknown_product_is_404(commerce_client: TestClient) -> None:
    _u, shop_id, _owner = _owner_with_tenant(commerce_client, "unk@example.uz")
    buyer = _buyer_token(commerce_client, "buyerz@example.uz")
    resp = commerce_client.post(
        "/api/commerce/cart/items",
        headers=_shop_headers(buyer, shop_id),
        json={"product_id": str(uuid4()), "quantity": 1},
    )
    assert resp.status_code == 404
