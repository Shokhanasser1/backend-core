"""commerce.product_images end-to-end (feature test — moves with the feature).

Uses the ``commerce_client`` fixture (root conftest): a running app with
ENABLED_MODULES=commerce, so the loader has mounted the router and registered RBAC,
and core/files is always on. Covers attach/list/serve/delete, the mandatory 403 (a
member cannot manage), the unsupported-type 422, a foreign/missing product (404)
and tenant isolation.

The app is reached only through the fixture — a feature never imports app.* (that
would cross the modules -> app layer boundary; import-linter enforces it).
"""

from io import BytesIO
from typing import cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image

from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


def _png_bytes(size: int = 512) -> bytes:
    """A real, decodable PNG — attach now generates a thumbnail (Pillow decodes it)."""
    buffer = BytesIO()
    Image.new("RGB", (size, size), (12, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


_PNG = _png_bytes()


def _create_product(client: TestClient, token: str, sku: str = "P1") -> str:
    resp = client.post(
        "/api/commerce/products",
        headers=_headers(token),
        json={"sku": sku, "name": "Item", "price_amount": 1000},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _attach(
    client: TestClient,
    token: str,
    product_id: str,
    *,
    data: bytes = _PNG,
    content_type: str = "image/png",
) -> Response:
    return cast(
        "Response",
        client.post(
            "/api/commerce/product-images",
            headers=_headers(token),
            data={"product_id": product_id},
            files={"upload": ("p.png", data, content_type)},
        ),
    )


def test_owner_attaches_lists_serves_and_deletes(commerce_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(commerce_client, "own@example.uz")
    product_id = _create_product(commerce_client, owner)

    attached = _attach(commerce_client, owner, product_id)
    assert attached.status_code == 201, attached.text
    image = attached.json()
    assert image["product_id"] == product_id
    assert image["thumbnail_file_id"] is not None  # generated at attach time
    image_id = image["id"]

    listed = commerce_client.get(
        "/api/commerce/product-images",
        headers=_headers(owner),
        params={"product_id": product_id},
    )
    assert listed.status_code == 200
    assert [i["id"] for i in listed.json()] == [image_id]

    content = commerce_client.get(
        f"/api/commerce/product-images/{image_id}/content", headers=_headers(owner)
    )
    assert content.status_code == 200
    assert content.content == _PNG
    assert content.headers["content-type"] == "image/png"

    assert (
        commerce_client.delete(
            f"/api/commerce/product-images/{image_id}", headers=_headers(owner)
        ).status_code
        == 204
    )
    # Gone: empty list and the bytes are no longer served.
    assert (
        commerce_client.get(
            "/api/commerce/product-images",
            headers=_headers(owner),
            params={"product_id": product_id},
        ).json()
        == []
    )
    assert (
        commerce_client.get(
            f"/api/commerce/product-images/{image_id}/content", headers=_headers(owner)
        ).status_code
        == 404
    )


def test_thumbnail_variant_is_resized(commerce_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(commerce_client, "thb@example.uz")
    product_id = _create_product(commerce_client, owner)
    image_id = _attach(commerce_client, owner, product_id, data=_png_bytes(512)).json()["id"]

    original = commerce_client.get(
        f"/api/commerce/product-images/{image_id}/content",
        headers=_headers(owner),
        params={"size": "original"},
    )
    assert original.status_code == 200
    assert original.content == _PNG  # the exact uploaded bytes, unchanged

    thumb = commerce_client.get(
        f"/api/commerce/product-images/{image_id}/content",
        headers=_headers(owner),
        params={"size": "thumb"},
    )
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/png"
    assert thumb.content != original.content  # a distinct, resized object
    with Image.open(BytesIO(thumb.content)) as img:
        assert max(img.size) <= 256  # capped at the configured max edge


def test_member_reads_but_cannot_manage(commerce_client: TestClient) -> None:
    _u, tenant_id, owner = _owner_with_tenant(commerce_client, "bos@example.uz")
    product_id = _create_product(commerce_client, owner)
    image_id = _attach(commerce_client, owner, product_id).json()["id"]
    member = _member_of(commerce_client, owner, tenant_id, "cle@example.uz")

    # Member holds commerce.product_image:read -> can list and fetch bytes.
    assert (
        commerce_client.get(
            "/api/commerce/product-images",
            headers=_headers(member),
            params={"product_id": product_id},
        ).status_code
        == 200
    )
    assert (
        commerce_client.get(
            f"/api/commerce/product-images/{image_id}/content", headers=_headers(member)
        ).status_code
        == 200
    )
    # Member lacks commerce.product_image:manage -> 403 (DoD negatives).
    assert _attach(commerce_client, member, product_id).status_code == 403
    assert (
        commerce_client.delete(
            f"/api/commerce/product-images/{image_id}", headers=_headers(member)
        ).status_code
        == 403
    )


def test_unsupported_type_rejected(commerce_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(commerce_client, "typ@example.uz")
    product_id = _create_product(commerce_client, owner)
    rejected = _attach(commerce_client, owner, product_id, data=b"not an image")
    assert rejected.status_code == 422


def test_attach_to_foreign_or_missing_product_is_404(commerce_client: TestClient) -> None:
    _ua, _ta, owner_a = _owner_with_tenant(commerce_client, "aaa@example.uz")
    _ub, _tb, owner_b = _owner_with_tenant(commerce_client, "bbb@example.uz")
    product_a = _create_product(commerce_client, owner_a)

    # B cannot attach to A's product (not in B's tenant) nor to a random id.
    assert _attach(commerce_client, owner_b, product_a).status_code == 404
    assert _attach(commerce_client, owner_b, str(uuid4())).status_code == 404


def test_tenant_isolation(commerce_client: TestClient) -> None:
    _ua, _ta, owner_a = _owner_with_tenant(commerce_client, "isa@example.uz")
    _ub, _tb, owner_b = _owner_with_tenant(commerce_client, "isb@example.uz")
    product_a = _create_product(commerce_client, owner_a)
    image_id = _attach(commerce_client, owner_a, product_a).json()["id"]

    # B sees no images for A's product and cannot fetch A's image bytes (404).
    assert (
        commerce_client.get(
            "/api/commerce/product-images",
            headers=_headers(owner_b),
            params={"product_id": product_a},
        ).json()
        == []
    )
    assert (
        commerce_client.get(
            f"/api/commerce/product-images/{image_id}/content", headers=_headers(owner_b)
        ).status_code
        == 404
    )
