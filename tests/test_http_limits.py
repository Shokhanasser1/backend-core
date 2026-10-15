"""HTTP edge hardening (audit batch 2): every request body is size-capped before
the app buffers it (413), file uploads get their own cap, and an inbound
X-Request-ID is accepted only in a safe shape (it is echoed and logged)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


def _assert_too_large(resp: object) -> None:
    assert resp.status_code == 413  # type: ignore[attr-defined]
    assert resp.json()["error"]["code"] == "payload_too_large"  # type: ignore[attr-defined]


def test_declared_oversize_body_is_refused_before_reading(client: TestClient) -> None:
    body = b"x" * (1024 * 1024 + 1)  # default cap: 1 MiB
    _assert_too_large(client.post("/api/billing/webhooks/payme", content=body))


def test_streamed_oversize_body_is_refused(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:  # chunked transfer: no Content-Length to trust
        for _ in range(20):
            yield b"y" * 65536

    _assert_too_large(
        client.post(
            "/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
        )
    )


def test_small_bodies_still_flow(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"email": "a@b.uz", "password": "x"})
    assert resp.status_code == 401


def test_file_upload_has_its_own_cap(test_settings: Settings, _clean_db: None) -> None:
    app = create_app(test_settings.model_copy(update={"files_max_upload_bytes": 1024}))
    with TestClient(app) as small:
        # Above the upload cap + multipart overhead: refused at the edge, before
        # authentication or parsing even run.
        resp = small.post(
            "/api/files",
            files={"upload": ("big.png", b"\x89PNG" + b"\x00" * 200_000, "image/png")},
        )
    _assert_too_large(resp)


@pytest.mark.parametrize("inbound", ["req-123", "0f8fad5b-d9cb-469f-a165-70867728950e"])
def test_safe_request_id_is_echoed(client: TestClient, inbound: str) -> None:
    resp = client.get("/health", headers={"X-Request-ID": inbound})
    assert resp.headers["x-request-id"] == inbound


@pytest.mark.parametrize("inbound", ["a" * 200, "evil\tid", 'x"}; drop'])
def test_unsafe_request_id_is_replaced(client: TestClient, inbound: str) -> None:
    resp = client.get("/health", headers={"X-Request-ID": inbound})
    echoed = resp.headers["x-request-id"]
    assert echoed != inbound
    assert len(echoed) == 32  # a freshly generated id
