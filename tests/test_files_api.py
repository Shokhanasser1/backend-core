"""core/files API end-to-end (integration).

Upload validates the content by magic bytes (the client Content-Type is not
trusted), download streams the bytes back inline, and the mandatory negatives are
covered: 401 without a token, 403 for a member lacking the permission, 422 for an
unsupported type and for an oversized upload, plus cross-tenant isolation (404).
"""

import hashlib
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.config import Settings
from app.main import create_app
from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration

# Valid PNG signature (8 bytes the sniffer checks) + filler; content is otherwise
# irrelevant — the sniffer only inspects the leading bytes.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _upload(
    client: TestClient,
    token: str,
    *,
    data: bytes = _PNG,
    filename: str = "logo.png",
    content_type: str = "image/png",
) -> Response:
    return cast(
        "Response",
        client.post(
            "/api/files",
            headers=_headers(token),
            files={"upload": (filename, data, content_type)},
        ),
    )


def test_owner_uploads_downloads_meta_and_deletes(client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(client, "own@example.uz")

    created = _upload(client, owner)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["content_type"] == "image/png"
    assert body["byte_size"] == len(_PNG)
    assert body["checksum_sha256"] == hashlib.sha256(_PNG).hexdigest()
    assert body["original_filename"] == "logo.png"
    file_id = body["id"]

    downloaded = client.get(f"/api/files/{file_id}", headers=_headers(owner))
    assert downloaded.status_code == 200
    assert downloaded.content == _PNG
    assert downloaded.headers["content-type"] == "image/png"
    assert downloaded.headers["x-content-type-options"] == "nosniff"

    meta = client.get(f"/api/files/{file_id}/meta", headers=_headers(owner))
    assert meta.status_code == 200
    assert meta.json()["id"] == file_id

    assert client.delete(f"/api/files/{file_id}", headers=_headers(owner)).status_code == 204
    # Gone after delete.
    assert client.get(f"/api/files/{file_id}", headers=_headers(owner)).status_code == 404


def test_unauthenticated_is_401(client: TestClient) -> None:
    assert _upload(client, "").status_code == 401  # empty bearer -> unauthenticated


def test_member_reads_but_cannot_upload_or_delete(client: TestClient) -> None:
    _u, tenant_id, owner = _owner_with_tenant(client, "bos2@example.uz")
    member = _member_of(client, owner, tenant_id, "cle2@example.uz")
    file_id = _upload(client, owner).json()["id"]

    # Member holds files.file:read -> can read metadata and bytes.
    assert client.get(f"/api/files/{file_id}/meta", headers=_headers(member)).status_code == 200
    assert client.get(f"/api/files/{file_id}", headers=_headers(member)).status_code == 200
    # Member lacks files.file:upload / :delete -> 403 (DoD negatives).
    assert _upload(client, member).status_code == 403
    assert client.delete(f"/api/files/{file_id}", headers=_headers(member)).status_code == 403


def test_unsupported_type_rejected(client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(client, "typ@example.uz")
    # Bytes that are not a supported image, even though the client claims image/png.
    rejected = _upload(client, owner, data=b"this is not an image", content_type="image/png")
    assert rejected.status_code == 422


def test_oversize_rejected(test_settings: Settings, _clean_db: None) -> None:
    # A dedicated app with a tiny cap so the oversize path is exercised cheaply.
    app = create_app(test_settings.model_copy(update={"files_max_upload_bytes": 16}))
    with TestClient(app) as small_client:
        _u, _t, owner = _owner_with_tenant(small_client, "big@example.uz")
        rejected = _upload(small_client, owner)  # _PNG is larger than 16 bytes
        assert rejected.status_code == 422


def test_tenant_isolation(client: TestClient) -> None:
    _ua, _ta, owner_a = _owner_with_tenant(client, "aa@example.uz")
    _ub, _tb, owner_b = _owner_with_tenant(client, "bb@example.uz")
    file_id = _upload(client, owner_a).json()["id"]

    # Tenant B sees neither the metadata nor the bytes of A's file (404, not 403).
    assert client.get(f"/api/files/{file_id}/meta", headers=_headers(owner_b)).status_code == 404
    assert client.get(f"/api/files/{file_id}", headers=_headers(owner_b)).status_code == 404
    assert client.delete(f"/api/files/{file_id}", headers=_headers(owner_b)).status_code == 404
