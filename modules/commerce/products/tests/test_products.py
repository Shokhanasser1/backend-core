"""commerce.products staff catalog end-to-end (feature test — moves with the feature).

Uses the ``commerce_client`` fixture (root conftest): a running app with
ENABLED_MODULES=commerce and a seeded currency, so the loader has mounted the
router and registered the RBAC. Exercises CRUD, the mandatory 403 (a member
cannot create), duplicate-SKU conflict, archive rules and tenant isolation.

The app is reached only through the fixture — a feature never imports app.* (that
would cross the modules -> app layer boundary; import-linter enforces it).
"""

import pytest
from fastapi.testclient import TestClient

from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


def test_owner_creates_lists_and_reads(commerce_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(commerce_client, "sho@example.uz")
    created = commerce_client.post(
        "/api/commerce/products",
        headers=_headers(owner),
        json={"sku": "A1", "name": "Chai", "price_amount": 25000},
    )
    assert created.status_code == 201, created.text
    product = created.json()
    assert product["currency"] == "UZS"
    assert product["status"] == "active"

    listed = commerce_client.get("/api/commerce/products", headers=_headers(owner))
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    got = commerce_client.get(f"/api/commerce/products/{product['id']}", headers=_headers(owner))
    assert got.status_code == 200
    assert got.json()["sku"] == "A1"


def test_member_reads_but_cannot_create(commerce_client: TestClient) -> None:
    _u, tenant_id, owner = _owner_with_tenant(commerce_client, "bos@example.uz")
    member = _member_of(commerce_client, owner, tenant_id, "cle@example.uz")

    # Member holds commerce.product:read -> can list.
    assert (
        commerce_client.get("/api/commerce/products", headers=_headers(member)).status_code == 200
    )
    # Member lacks commerce.product:create -> 403 (DoD negative test).
    blocked = commerce_client.post(
        "/api/commerce/products",
        headers=_headers(member),
        json={"sku": "X", "name": "Y", "price_amount": 100},
    )
    assert blocked.status_code == 403


def test_update_then_archive(commerce_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(commerce_client, "upd@example.uz")
    pid = commerce_client.post(
        "/api/commerce/products",
        headers=_headers(owner),
        json={"sku": "S", "name": "Old", "price_amount": 100},
    ).json()["id"]

    updated = commerce_client.patch(
        f"/api/commerce/products/{pid}",
        headers=_headers(owner),
        json={"name": "New", "price_amount": 200},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "New"
    assert updated.json()["price_amount"] == 200

    archived = commerce_client.post(
        f"/api/commerce/products/{pid}/archive", headers=_headers(owner)
    )
    assert archived.json()["status"] == "archived"
    # Archived products cannot be updated (invariant -> 422).
    assert (
        commerce_client.patch(
            f"/api/commerce/products/{pid}", headers=_headers(owner), json={"name": "Z"}
        ).status_code
        == 422
    )


def test_duplicate_sku_conflicts(commerce_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(commerce_client, "dup@example.uz")
    body = {"sku": "DUP", "name": "A", "price_amount": 1}
    assert (
        commerce_client.post(
            "/api/commerce/products", headers=_headers(owner), json=body
        ).status_code
        == 201
    )
    assert (
        commerce_client.post(
            "/api/commerce/products", headers=_headers(owner), json=body
        ).status_code
        == 409
    )


def test_tenant_isolation(commerce_client: TestClient) -> None:
    _ua, _ta, owner_a = _owner_with_tenant(commerce_client, "aaa@example.uz")
    _ub, _tb, owner_b = _owner_with_tenant(commerce_client, "bbb@example.uz")
    pid = commerce_client.post(
        "/api/commerce/products",
        headers=_headers(owner_a),
        json={"sku": "A", "name": "secret", "price_amount": 1},
    ).json()["id"]

    # Tenant B sees neither A's product by id (404, not 403) nor in the list.
    assert (
        commerce_client.get(f"/api/commerce/products/{pid}", headers=_headers(owner_b)).status_code
        == 404
    )
    assert (
        commerce_client.get("/api/commerce/products", headers=_headers(owner_b)).json()["total"]
        == 0
    )
