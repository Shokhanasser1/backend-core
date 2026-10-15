"""Tenant hardening (audit batch 2): invitations travel only by email and bind to
the invited address, acceptance is single-use under concurrency, and a suspended
tenant is locked out of its API and token exchange."""

import asyncio
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.tenants.service import TenantService
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_MIGRATOR
from shared.errors import ConflictError, NotFoundError
from shared.events import EventBus
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import (
    _headers,
    _login,
    _owner_with_tenant,
    _register,
    _tenant_token,
    invitation_token,
)

pytestmark = pytest.mark.integration


def _invite(client: TestClient, owner_tenant: str, email: str) -> dict[str, Any]:
    resp = client.post(
        "/api/tenants/invitations",
        headers=_headers(owner_tenant),
        json={"email": email, "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _accept(client: TestClient, user_token: str, token: str) -> int:
    resp = client.post(
        "/api/tenants/invitations/accept",
        headers=_headers(user_token),
        json={"invitation_token": token},
    )
    return cast("int", resp.status_code)


def test_invitation_token_is_emailed_never_returned(client: TestClient) -> None:
    _u, tenant_id, owner_tenant = _owner_with_tenant(client, "boss@example.uz")

    body = _invite(client, owner_tenant, "New@Example.uz")

    assert "invitation_token" not in body
    assert body["invitation"]["email"] == "new@example.uz"
    token = invitation_token(client, "new@example.uz")  # only inside the emailed link
    _register(client, "new@example.uz")
    assert _accept(client, _login(client, "new@example.uz")["access_token"], token) == 200
    member = _tenant_token(client, _login(client, "new@example.uz")["access_token"], tenant_id)
    assert client.get("/api/tenants/current", headers=_headers(member)).status_code == 200


def test_invitation_email_is_queued_in_the_tenant_outbox(client: TestClient) -> None:
    _u, tenant_id, owner_tenant = _owner_with_tenant(client, "out@example.uz")
    _invite(client, owner_tenant, "guest@example.uz")

    async def rows() -> list[dict[str, Any]]:
        app = cast("FastAPI", client.app)
        engine = create_async_engine(app.state.settings.database_maintenance_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT tenant_id, channel, recipient FROM notification_outbox "
                        "WHERE template_key = 'tenants.invitation'"
                    )
                )
                return [dict(r) for r in result.mappings()]
        finally:
            await engine.dispose()

    queued = asyncio.run(rows())
    assert len(queued) == 1
    assert queued[0]["channel"] == "email"
    assert queued[0]["recipient"] == "guest@example.uz"
    assert str(queued[0]["tenant_id"]) == tenant_id


def test_invitation_cannot_be_accepted_by_another_account(client: TestClient) -> None:
    _u, _tenant_id, owner_tenant = _owner_with_tenant(client, "own2@example.uz")
    _invite(client, owner_tenant, "invited@example.uz")
    token = invitation_token(client, "invited@example.uz")

    _register(client, "thief@example.uz")
    thief = _login(client, "thief@example.uz")["access_token"]

    assert _accept(client, thief, token) == 403
    # Still pending for the rightful invitee.
    _register(client, "invited@example.uz")
    assert _accept(client, _login(client, "invited@example.uz")["access_token"], token) == 200


def test_concurrent_accept_is_single_use(client: TestClient, role_urls: dict[str, str]) -> None:
    _u, _tenant_id, owner_tenant = _owner_with_tenant(client, "con@example.uz")
    _invite(client, owner_tenant, "twice@example.uz")
    token = invitation_token(client, "twice@example.uz")
    user = _register(client, "twice@example.uz")
    ctx = TenantContext(tenant_id=None, actor=Actor(kind="user", id=user["id"]), request_id=None)

    async def race() -> list[object]:
        engine = create_async_engine(role_urls[ROLE_MAINTENANCE])
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def accept() -> object:
            try:
                async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                    service = TenantService(uow, EventBus(), ctx)
                    return await service.accept_invitation(
                        token, accepting_email="twice@example.uz"
                    )
            except (NotFoundError, ConflictError) as exc:
                return exc

        try:
            return list(await asyncio.gather(accept(), accept()))
        finally:
            await engine.dispose()

    results = asyncio.run(race())

    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1  # the loser gets a domain error, never an IntegrityError
    assert isinstance(errors[0], (NotFoundError, ConflictError))


async def _set_tenant_status(role_urls: dict[str, str], tenant_id: str, status: str) -> None:
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET status = :s WHERE id = :id"),
                {"s": status, "id": UUID(tenant_id)},
            )
    finally:
        await engine.dispose()


def test_suspended_tenant_is_locked_out(client: TestClient, role_urls: dict[str, str]) -> None:
    user_token, tenant_id, tenant_token = _owner_with_tenant(client, "sus@example.uz")
    assert client.get("/api/tenants/current", headers=_headers(tenant_token)).status_code == 200

    asyncio.run(_set_tenant_status(role_urls, tenant_id, "suspended"))

    # An already-issued tenant token stops working...
    assert client.get("/api/tenants/current", headers=_headers(tenant_token)).status_code == 403
    # ...and no new one can be minted.
    exchange = client.post(f"/api/auth/tenants/{tenant_id}/token", headers=_headers(user_token))
    assert exchange.status_code == 403

    asyncio.run(_set_tenant_status(role_urls, tenant_id, "active"))
    assert client.get("/api/tenants/current", headers=_headers(tenant_token)).status_code == 200


def test_suspended_shop_storefront_is_closed(
    commerce_client: TestClient, role_urls: dict[str, str]
) -> None:
    _u, shop_id, _owner = _owner_with_tenant(commerce_client, "shop@example.uz")
    _register(commerce_client, "buyer@example.uz")
    buyer = _login(commerce_client, "buyer@example.uz")["access_token"]
    headers = {**_headers(buyer), "X-Shop-Tenant": shop_id}
    # The buyer's own order list: 200 (empty) while the shop is open.
    assert commerce_client.get("/api/commerce/orders", headers=headers).status_code == 200

    asyncio.run(_set_tenant_status(role_urls, shop_id, "suspended"))

    closed = commerce_client.get("/api/commerce/orders", headers=headers)
    assert closed.status_code == 404
    assert closed.json()["error"]["detail"] == "shop not found"


def test_invited_event_carries_no_email(client: TestClient) -> None:
    _u, _tenant_id, owner_tenant = _owner_with_tenant(client, "pii@example.uz")
    captured: list[dict[str, Any]] = []
    app = cast("FastAPI", client.app)
    original = app.state.bus.publish

    async def spy(envelope: Any) -> None:
        captured.append({"name": envelope.name, "payload": dict(envelope.payload)})
        await original(envelope)

    app.state.bus.publish = spy
    try:
        _invite(client, owner_tenant, f"{uuid4().hex[:8]}@example.uz")
    finally:
        app.state.bus.publish = original

    invited = [e for e in captured if e["name"] == "tenants.member.invited"]
    assert len(invited) == 1
    assert "email" not in invited[0]["payload"]
