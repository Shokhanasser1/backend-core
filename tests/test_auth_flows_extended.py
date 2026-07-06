"""Extended auth + tenant flows: 2FA, password reset/change, logout, member
management (interfaces §3.1/§3.2; threat model V2/V3)."""

import pyotp
import pytest
from fastapi.testclient import TestClient

from tests.test_auth_flows import (
    PASSWORD,
    _headers,
    _login,
    _owner_with_tenant,
    _register,
    _tenant_token,
)

pytestmark = pytest.mark.integration


def test_full_two_factor_enrollment_and_login(client: TestClient) -> None:
    _register(client, "tfa@example.uz")
    token = _login(client, "tfa@example.uz")["access_token"]

    setup = client.post("/api/auth/2fa/enable", headers=_headers(token))
    assert setup.status_code == 200
    secret = setup.json()["secret"]

    confirm = client.post(
        "/api/auth/2fa/confirm",
        headers=_headers(token),
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 200
    recovery_codes = confirm.json()["codes"]
    assert len(recovery_codes) == 10

    # Now login returns a 2FA challenge instead of tokens.
    challenge = client.post(
        "/api/auth/login", json={"email": "tfa@example.uz", "password": PASSWORD}
    )
    assert challenge.status_code == 200
    body = challenge.json()
    assert body["two_factor_required"] is True

    verified = client.post(
        "/api/auth/2fa/verify",
        json={"challenge_token": body["challenge_token"], "code": pyotp.TOTP(secret).now()},
    )
    assert verified.status_code == 200
    assert "access_token" in verified.json()


def test_login_with_recovery_code(client: TestClient) -> None:
    _register(client, "rec@example.uz")
    token = _login(client, "rec@example.uz")["access_token"]
    secret = client.post("/api/auth/2fa/enable", headers=_headers(token)).json()["secret"]
    codes = client.post(
        "/api/auth/2fa/confirm", headers=_headers(token), json={"code": pyotp.TOTP(secret).now()}
    ).json()["codes"]

    challenge = client.post(
        "/api/auth/login", json={"email": "rec@example.uz", "password": PASSWORD}
    ).json()
    verified = client.post(
        "/api/auth/2fa/verify",
        json={"challenge_token": challenge["challenge_token"], "code": codes[0]},
    )
    assert verified.status_code == 200


def test_change_password_revokes_sessions(client: TestClient) -> None:
    _register(client, "cp@example.uz")
    tokens = _login(client, "cp@example.uz")
    resp = client.post(
        "/api/auth/change-password",
        headers=_headers(tokens["access_token"]),
        json={"current_password": PASSWORD, "new_password": "N3w-password-99"},
    )
    assert resp.status_code == 200
    # Old refresh token was revoked.
    assert (
        client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )
    # New password works, old one does not.
    assert (
        client.post(
            "/api/auth/login", json={"email": "cp@example.uz", "password": "N3w-password-99"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": "cp@example.uz", "password": PASSWORD}
        ).status_code
        == 401
    )


def test_logout_revokes_refresh(client: TestClient) -> None:
    _register(client, "lo@example.uz")
    tokens = _login(client, "lo@example.uz")
    out = client.post(
        "/api/auth/logout",
        headers=_headers(tokens["access_token"]),
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert out.status_code == 200
    assert (
        client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_password_reset_flow_no_enumeration(client: TestClient) -> None:
    _register(client, "pr@example.uz")
    # Unknown and known emails both return 200 with no leak.
    assert (
        client.post(
            "/api/auth/password-reset/request", json={"email": "nobody@example.uz"}
        ).status_code
        == 200
    )
    requested = client.post("/api/auth/password-reset/request", json={"email": "pr@example.uz"})
    assert requested.status_code == 200
    reset_token = requested.json()["reset_token"]
    assert reset_token is not None

    done = client.post(
        "/api/auth/password-reset",
        json={"reset_token": reset_token, "new_password": "Reset-pw-123"},
    )
    assert done.status_code == 200
    assert (
        client.post(
            "/api/auth/login", json={"email": "pr@example.uz", "password": "Reset-pw-123"}
        ).status_code
        == 200
    )


def _member_of(client: TestClient, owner_tenant: str, tenant_id: str, email: str) -> str:
    """Invite + accept a member; return their tenant-scoped token."""
    invited = client.post(
        "/api/tenants/invitations",
        headers=_headers(owner_tenant),
        json={"email": email, "role": "member"},
    )
    assert invited.status_code == 201, invited.text
    _register(client, email)
    user_token = _login(client, email)["access_token"]
    client.post(
        "/api/tenants/invitations/accept",
        headers=_headers(user_token),
        json={"invitation_token": invited.json()["invitation_token"]},
    )
    return _tenant_token(client, user_token, tenant_id)


def test_member_management(client: TestClient) -> None:
    _owner, tenant_id, owner_tenant = _owner_with_tenant(client, "mgmt@example.uz")
    member_tenant = _member_of(client, owner_tenant, tenant_id, "worker@example.uz")

    # Owner lists members (owner + worker).
    members = client.get("/api/tenants/members", headers=_headers(owner_tenant))
    assert members.status_code == 200
    assert members.json()["total"] == 2

    worker_id = next(m["user_id"] for m in members.json()["items"] if m["role_code"] == "member")

    # Promote the worker to admin.
    promoted = client.patch(
        f"/api/tenants/members/{worker_id}/role",
        headers=_headers(owner_tenant),
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["role_code"] == "admin"

    # Update tenant settings (owner has tenant:update).
    updated = client.patch(
        "/api/tenants/current", headers=_headers(owner_tenant), json={"name": "Renamed"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"

    # Remove the member.
    removed = client.request(
        "DELETE", f"/api/tenants/members/{worker_id}", headers=_headers(owner_tenant)
    )
    assert removed.status_code == 200
    assert client.get("/api/tenants/members", headers=_headers(owner_tenant)).json()["total"] == 1

    # The removed member's tenant token no longer grants access.
    assert client.get("/api/tenants/current", headers=_headers(member_tenant)).status_code == 403


def test_revoke_invitation(client: TestClient) -> None:
    _owner, _tenant_id, owner_tenant = _owner_with_tenant(client, "revoke@example.uz")
    invited = client.post(
        "/api/tenants/invitations",
        headers=_headers(owner_tenant),
        json={"email": "pending@example.uz", "role": "member"},
    )
    invitation_id = invited.json()["invitation"]["id"]
    resp = client.request(
        "DELETE", f"/api/tenants/invitations/{invitation_id}", headers=_headers(owner_tenant)
    )
    assert resp.status_code == 200
