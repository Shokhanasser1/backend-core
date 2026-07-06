"""Authenticated billing endpoints (/api/billing) through the HTTP app.

Exercises the read/manage routes end-to-end and the mandatory negative 403 (a
member without billing:manage cannot start a subscription). Builds its own app so
payment providers / seeded plans are under the test's control; reuses the auth
flow helpers to obtain owner/member tenant tokens.
"""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.main import create_app
from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_MIGRATOR, ROLE_USER
from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration

PAYME_KEY = "test-merchant-key"
PRO_PRICE = 50_000


def _billing_settings(
    role_urls: dict[str, str], redis_url: str, *, payme: bool = False
) -> Settings:
    extra: dict[str, str] = {}
    if payme:
        extra = {
            "enabled_payment_providers": "payme",
            "payme_merchant_id": "merchant-1",
            "payme_merchant_key": PAYME_KEY,
        }
    return Settings(
        _env_file=None,
        app_env="test",
        log_level="WARNING",
        database_url=role_urls[ROLE_USER],
        database_migrator_url=role_urls[ROLE_MIGRATOR],
        database_maintenance_url=role_urls[ROLE_MAINTENANCE],
        redis_url=redis_url,
        **extra,  # type: ignore[arg-type]
    )


async def _seed_plan(role_urls: dict[str, str], code: str, price: int) -> None:
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO currencies (code, exponent, name) VALUES ('UZS', 0, 'Som') "
                    "ON CONFLICT (code) DO NOTHING"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO plans (id, code, name, price_amount) "
                    "VALUES (:id, :code, CAST(:name AS jsonb), :price)"
                ),
                {"id": uuid4(), "code": code, "name": '{"ru": "Pro", "uz": "Pro"}', "price": price},
            )
    finally:
        await engine.dispose()


def test_owner_lists_plans_and_reads_subscription(
    role_urls: dict[str, str], redis_url: str, _clean_db: None
) -> None:
    asyncio.run(_seed_plan(role_urls, "pro", PRO_PRICE))
    app = create_app(_billing_settings(role_urls, redis_url))
    with TestClient(app) as client:
        _, _tenant_id, tenant_token = _owner_with_tenant(client, "plans@example.uz")

        plans = client.get("/api/billing/plans", headers=_headers(tenant_token))
        assert plans.status_code == 200
        pro = next(p for p in plans.json() if p["code"] == "pro")
        assert pro["price"] == {"amount": PRO_PRICE, "currency": "UZS"}

        providers = client.get("/api/billing/providers", headers=_headers(tenant_token))
        assert providers.status_code == 200
        assert providers.json() == []  # none enabled in this app

        # No worker runs in tests, so the auto-subscribe job never fires.
        sub = client.get("/api/billing/subscription", headers=_headers(tenant_token))
        assert sub.status_code == 200
        assert sub.json() is None


def test_billing_requires_auth_and_tenant_scope(
    role_urls: dict[str, str], redis_url: str, _clean_db: None
) -> None:
    app = create_app(_billing_settings(role_urls, redis_url))
    with TestClient(app) as client:
        # No token at all -> 401.
        assert client.get("/api/billing/plans").status_code == 401
        # User-scope token (no tenant claim) -> 403 on a tenant-scoped route.
        user_token, _tenant_id, _tenant_token = _owner_with_tenant(client, "scope@example.uz")
        anon = client.get("/api/billing/plans", headers=_headers(user_token))
        assert anon.status_code == 403


def test_member_can_read_but_not_manage_subscription(
    role_urls: dict[str, str], redis_url: str, _clean_db: None
) -> None:
    asyncio.run(_seed_plan(role_urls, "free", 0))
    app = create_app(_billing_settings(role_urls, redis_url))
    with TestClient(app) as client:
        _owner, tenant_id, owner_tenant = _owner_with_tenant(client, "boss@example.uz")
        member_tenant = _member_of(client, owner_tenant, tenant_id, "worker@example.uz")

        # Member has billing.plan:read -> can list plans.
        assert client.get("/api/billing/plans", headers=_headers(member_tenant)).status_code == 200

        # Member lacks billing.subscription:manage -> 403 on start (negative test, DoD).
        start = client.post(
            "/api/billing/subscription",
            headers=_headers(member_tenant),
            json={"plan_code": "free", "provider": "payme"},
        )
        assert start.status_code == 403


def test_owner_starts_subscription_gets_checkout(
    role_urls: dict[str, str], redis_url: str, _clean_db: None
) -> None:
    asyncio.run(_seed_plan(role_urls, "pro", PRO_PRICE))
    app = create_app(_billing_settings(role_urls, redis_url, payme=True))
    with TestClient(app) as client:
        _owner, _tenant_id, owner_tenant = _owner_with_tenant(client, "buy@example.uz")

        providers = client.get("/api/billing/providers", headers=_headers(owner_tenant))
        assert [p["code"] for p in providers.json()] == ["payme"]

        start = client.post(
            "/api/billing/subscription",
            headers=_headers(owner_tenant),
            json={"plan_code": "pro", "provider": "payme"},
        )
        assert start.status_code == 201, start.text
        body = start.json()
        assert body["provider"] == "payme"
        assert body["checkout_url"].startswith("https://checkout.paycom.uz/")


def test_cancel_without_subscription_is_404(
    role_urls: dict[str, str], redis_url: str, _clean_db: None
) -> None:
    app = create_app(_billing_settings(role_urls, redis_url))
    with TestClient(app) as client:
        _owner, _tenant_id, owner_tenant = _owner_with_tenant(client, "nocancel@example.uz")
        resp = client.post("/api/billing/subscription/cancel", headers=_headers(owner_tenant))
        assert resp.status_code == 404
