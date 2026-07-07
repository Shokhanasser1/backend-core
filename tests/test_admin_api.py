"""Admin scaffold end-to-end through the HTTP app (interfaces §3.6, §5.4).

The menu (`/api/admin/screens`) and the audit screen (`/api/admin/audit`) are
both gated by RBAC: owner/admin pass, a plain member gets 403, no token gets 401.
The audit screen is scoped to the caller's tenant. Reuses the auth-flow helpers to
obtain owner/member tenant tokens; seeds audit rows directly since no worker runs
in tests (the wildcard sink is a reliable arq handler).
"""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from shared.db_provisioning import ROLE_MAINTENANCE
from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


async def _seed_audit(role_urls: dict[str, str], tenant_id: str, action: str) -> None:
    engine = create_async_engine(role_urls[ROLE_MAINTENANCE])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO audit_log (id, tenant_id, action, payload, created_at) "
                    "VALUES (:id, :tenant, :action, '{}'::jsonb, now())"
                ),
                {"id": uuid4(), "tenant": tenant_id, "action": action},
            )
    finally:
        await engine.dispose()


def test_owner_sees_audit_screen_in_menu(client: TestClient, role_urls: dict[str, str]) -> None:
    _user, _tenant_id, owner_tenant = _owner_with_tenant(client, "menu@example.uz")
    menu = client.get("/api/admin/screens", headers=_headers(owner_tenant))
    assert menu.status_code == 200
    screens = {s["slug"]: s for s in menu.json()}
    assert "audit" in screens
    assert screens["audit"]["path"] == "/api/admin/audit"
    assert screens["audit"]["permission"] == "audit.record:read"


def test_owner_reads_and_filters_audit_log(client: TestClient, role_urls: dict[str, str]) -> None:
    _user, tenant_id, owner_tenant = _owner_with_tenant(client, "reader@example.uz")
    asyncio.run(_seed_audit(role_urls, tenant_id, "auth.user.login_failed"))
    asyncio.run(_seed_audit(role_urls, tenant_id, "billing.payment.succeeded"))

    all_rows = client.get("/api/admin/audit", headers=_headers(owner_tenant))
    assert all_rows.status_code == 200
    assert all_rows.json()["total"] == 2

    filtered = client.get(
        "/api/admin/audit",
        params={"action_prefix": "auth."},
        headers=_headers(owner_tenant),
    )
    assert filtered.status_code == 200
    body = filtered.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "auth.user.login_failed"


def test_audit_is_tenant_scoped(client: TestClient, role_urls: dict[str, str]) -> None:
    _ua, tenant_a, _ta_token = _owner_with_tenant(client, "ta@example.uz")
    _ub, _tenant_b, tb_token = _owner_with_tenant(client, "tb@example.uz")
    asyncio.run(_seed_audit(role_urls, tenant_a, "secret.action"))

    # Tenant B's owner must not see tenant A's audit row.
    resp = client.get("/api/admin/audit", headers=_headers(tb_token))
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_member_is_forbidden(client: TestClient) -> None:
    _user, tenant_id, owner_tenant = _owner_with_tenant(client, "boss@example.uz")
    member_tenant = _member_of(client, owner_tenant, tenant_id, "peon@example.uz")

    # A member holds neither admin.screen:read nor audit.record:read (DoD 403 test).
    assert client.get("/api/admin/screens", headers=_headers(member_tenant)).status_code == 403
    assert client.get("/api/admin/audit", headers=_headers(member_tenant)).status_code == 403


def test_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/admin/screens").status_code == 401
    assert client.get("/api/admin/audit").status_code == 401
