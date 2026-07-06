"""End-to-end auth + tenants flows through the HTTP app (threat model V2/V3/V7).

Exercises the whole vertical: register -> login -> create tenant -> select
tenant -> permission-protected access, plus the mandatory negative tests.
"""

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PASSWORD = "S3cure-pw-123"


def _register(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    resp = client.post("/api/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return cast("dict[str, Any]", resp.json())


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _tenant_token(client: TestClient, user_token: str, tenant_id: str) -> str:
    resp = client.post(f"/api/auth/tenants/{tenant_id}/token", headers=_headers(user_token))
    assert resp.status_code == 200, resp.text
    return cast("str", resp.json()["access_token"])


def _owner_with_tenant(client: TestClient, email: str) -> tuple[str, str, str]:
    """Return (user_token, tenant_id, tenant_token) for a fresh owner."""
    _register(client, email)
    user_token = _login(client, email)["access_token"]
    created = client.post(
        "/api/tenants",
        headers=_headers(user_token),
        json={"name": "Acme", "slug": f"acme-{email[:3]}"},
    )
    assert created.status_code == 201, created.text
    tenant_id = created.json()["id"]
    return user_token, tenant_id, _tenant_token(client, user_token, tenant_id)


def test_register_login_me(client: TestClient) -> None:
    profile = _register(client, "a@example.uz")
    assert profile["email"] == "a@example.uz"
    assert profile["two_factor_enabled"] is False

    tokens = _login(client, "a@example.uz")
    me = client.get("/api/auth/me", headers=_headers(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.uz"


def test_register_rejects_mass_assignment(client: TestClient) -> None:
    # extra="forbid" blocks unexpected fields (threat model V7).
    resp = client.post(
        "/api/auth/register",
        json={"email": "b@example.uz", "password": PASSWORD, "is_superuser": True},
    )
    assert resp.status_code == 422


def test_login_wrong_password_401(client: TestClient) -> None:
    _register(client, "c@example.uz")
    resp = client.post("/api/auth/login", json={"email": "c@example.uz", "password": "nope"})
    assert resp.status_code == 401


def test_login_unknown_user_same_error(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"email": "ghost@example.uz", "password": "x"})
    assert resp.status_code == 401  # no user enumeration: identical to wrong password


def test_permission_flow_owner_can_read_tenant(client: TestClient) -> None:
    _, _tenant_id, tenant_token = _owner_with_tenant(client, "owner@example.uz")
    resp = client.get("/api/tenants/current", headers=_headers(tenant_token))
    assert resp.status_code == 200
    assert resp.json()["name"] == "Acme"


def test_permission_requires_tenant_token(client: TestClient) -> None:
    user_token, _tenant_id, _ = _owner_with_tenant(client, "u2@example.uz")
    # User-scope token (no tenant claim) → 403 on a tenant-scoped route.
    resp = client.get("/api/tenants/current", headers=_headers(user_token))
    assert resp.status_code == 403


def test_permission_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/tenants/current")
    assert resp.status_code == 401


def test_member_without_permission_gets_403(client: TestClient) -> None:
    _owner_user, tenant_id, owner_tenant = _owner_with_tenant(client, "owner2@example.uz")

    invited = client.post(
        "/api/tenants/invitations",
        headers=_headers(owner_tenant),
        json={"email": "member@example.uz", "role": "member"},
    )
    assert invited.status_code == 201, invited.text
    invitation_token = invited.json()["invitation_token"]

    _register(client, "member@example.uz")
    member_user = _login(client, "member@example.uz")["access_token"]
    accepted = client.post(
        "/api/tenants/invitations/accept",
        headers=_headers(member_user),
        json={"invitation_token": invitation_token},
    )
    assert accepted.status_code == 200, accepted.text
    member_tenant = _tenant_token(client, member_user, tenant_id)

    # Member can read the tenant...
    assert client.get("/api/tenants/current", headers=_headers(member_tenant)).status_code == 200
    # ...but cannot invite (lacks tenants.member:invite) → 403.
    forbidden = client.post(
        "/api/tenants/invitations",
        headers=_headers(member_tenant),
        json={"email": "x@example.uz", "role": "member"},
    )
    assert forbidden.status_code == 403


def test_refresh_rotation_and_reuse_detection(client: TestClient) -> None:
    _register(client, "r@example.uz")
    tokens = _login(client, "r@example.uz")
    first_refresh = tokens["refresh_token"]

    rotated = client.post("/api/auth/refresh", json={"refresh_token": first_refresh})
    assert rotated.status_code == 200
    second_refresh = rotated.json()["refresh_token"]
    assert second_refresh != first_refresh

    # Reusing the rotated token is detected → whole family revoked (V3).
    reused = client.post("/api/auth/refresh", json={"refresh_token": first_refresh})
    assert reused.status_code == 401
    # The descendant is revoked too.
    after = client.post("/api/auth/refresh", json={"refresh_token": second_refresh})
    assert after.status_code == 401


def test_last_owner_cannot_be_removed(client: TestClient) -> None:
    owner_user, _tenant_id, owner_tenant = _owner_with_tenant(client, "solo@example.uz")
    me = client.get("/api/auth/me", headers=_headers(owner_user)).json()
    resp = client.request(
        "DELETE", f"/api/tenants/members/{me['id']}", headers=_headers(owner_tenant)
    )
    assert resp.status_code == 422  # InvariantViolationError: last owner
