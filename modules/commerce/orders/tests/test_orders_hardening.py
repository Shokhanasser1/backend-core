"""commerce.orders hardening (feature test — moves with the feature): a late
payment revives an order canceled by checkout expiry (money moved), but only for
that order's own payment; the buyer's order list is paginated; every storefront
route requires authentication; staff listing is permission-checked in the service
too, not only on the route."""

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.auth.access_service import AccessService
from core.notifications.models import NotificationOutbox
from core.tenants.service import TenantService
from modules.commerce.orders.service import OrderService
from modules.commerce.orders.subscribers import cancel_order_on_expiry, mark_order_paid
from shared.context import Actor, TenantContext
from shared.errors import PermissionDeniedError
from shared.events import EventBus, EventEnvelope, bus
from shared.handler_runtime import HandlerRuntime, reset_handler_runtime, set_handler_runtime
from shared.ids import new_uuid7
from shared.pagination import Page
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _login, _owner_with_tenant, _register
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


def _shop(token: str, shop_id: str) -> dict[str, str]:
    return {**_headers(token), "X-Shop-Tenant": shop_id}


def _place(client: TestClient, buyer: str, shop_id: str, pid: str) -> dict[str, Any]:
    resp = client.post(
        "/api/commerce/orders",
        headers=_shop(buyer, shop_id),
        json={"provider": "payme", "items": [{"product_id": pid, "quantity": 1}]},
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _setup(client: TestClient, tag: str) -> tuple[str, str, str]:
    _u, shop_id, owner = _owner_with_tenant(client, f"{tag}@example.uz")
    pid = client.post(
        "/api/commerce/products",
        headers=_headers(owner),
        json={"sku": f"S-{tag}", "name": "Tea", "price_amount": 700},
    ).json()["id"]
    _register(client, f"{tag}-buyer@example.uz")
    buyer = cast("str", _login(client, f"{tag}-buyer@example.uz")["access_token"])
    return shop_id, pid, buyer


def _deliver(
    handler: object,
    database_url: str,
    shop_id: str,
    name: str,
    payload: dict[str, Any],
) -> None:
    ctx = TenantContext(
        tenant_id=UUID(shop_id), actor=Actor(kind="integration", id="payme"), request_id=None
    )
    envelope = EventEnvelope(
        event_id=new_uuid7(),
        name=name,
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=UUID(shop_id),
        actor=ctx.actor,
        payload={"purpose": "commerce.order", "currency": "UZS", "provider": "payme", **payload},
    )

    async def run() -> None:
        # Own engine per delivery: a pooled connection must not outlive its loop.
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                # A private bus: post-commit events must not reach the app's arq
                # pool, which lives on the TestClient's loop.
                token = set_handler_runtime(HandlerRuntime(uow=uow, ctx=ctx, bus=EventBus()))
                try:
                    await handler(envelope)  # type: ignore[operator]
                finally:
                    reset_handler_runtime(token)
        finally:
            await engine.dispose()

    asyncio.run(run())


def _db(client: TestClient) -> str:
    return cast("str", cast("FastAPI", client.app).state.settings.database_url)


def _status(client: TestClient, buyer: str, shop_id: str, order_id: str) -> str:
    resp = client.get(f"/api/commerce/orders/{order_id}", headers=_shop(buyer, shop_id))
    return cast("str", resp.json()["status"])


def test_late_payment_revives_an_order_canceled_by_expiry(
    commerce_payments_client: TestClient,
) -> None:
    client = commerce_payments_client
    shop_id, pid, buyer = _setup(client, "late")
    placed = _place(client, buyer, shop_id, pid)
    ref = {"reference": placed["order_id"], "payment_id": placed["payment_id"], "amount": 700}
    _deliver(cancel_order_on_expiry, _db(client), shop_id, "billing.payment.expired", ref)
    assert _status(client, buyer, shop_id, placed["order_id"]) == "canceled"

    _deliver(
        mark_order_paid,
        _db(client),
        shop_id,
        "billing.payment.succeeded",
        {**ref, "late": True},
    )

    assert _status(client, buyer, shop_id, placed["order_id"]) == "paid"

    async def receipts() -> int:
        app = cast("FastAPI", client.app)
        engine = create_async_engine(app.state.settings.database_maintenance_url)
        try:
            async with engine.connect() as conn:
                return int(
                    (
                        await conn.execute(
                            select(func.count())
                            .select_from(NotificationOutbox)
                            .where(NotificationOutbox.template_key == "commerce.order_paid")
                        )
                    ).scalar_one()
                )
        finally:
            await engine.dispose()

    assert asyncio.run(receipts()) == 1


def test_late_payment_of_another_payment_does_not_revive(
    commerce_payments_client: TestClient,
) -> None:
    client = commerce_payments_client
    shop_id, pid, buyer = _setup(client, "other")
    placed = _place(client, buyer, shop_id, pid)
    ref = {"reference": placed["order_id"], "payment_id": placed["payment_id"], "amount": 700}
    _deliver(cancel_order_on_expiry, _db(client), shop_id, "billing.payment.expired", ref)

    foreign = {**ref, "payment_id": str(uuid4()), "late": True}
    _deliver(mark_order_paid, _db(client), shop_id, "billing.payment.succeeded", foreign)

    assert _status(client, buyer, shop_id, placed["order_id"]) == "canceled"


def test_buyer_order_list_is_paginated(commerce_payments_client: TestClient) -> None:
    client = commerce_payments_client
    shop_id, pid, buyer = _setup(client, "page")
    for _ in range(3):
        _place(client, buyer, shop_id, pid)

    resp = client.get("/api/commerce/orders?limit=2&offset=0", headers=_shop(buyer, shop_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert all(len(order["items"]) == 1 for order in body["items"])


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/commerce/orders"),
        ("POST", "/api/commerce/orders"),
        ("GET", "/api/commerce/orders/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_orders_require_authentication(
    commerce_payments_client: TestClient, method: str, path: str
) -> None:
    resp = commerce_payments_client.request(
        method, path, headers={"X-Shop-Tenant": str(UUID(int=1))}
    )
    assert resp.status_code == 401


def test_staff_listing_is_permission_checked_in_the_service(
    commerce_payments_client: TestClient,
) -> None:
    client = commerce_payments_client
    _u, shop_id, owner = _owner_with_tenant(client, "svc@example.uz")
    _member_of(client, owner, shop_id, "clerk2@example.uz")
    clerk_id = client.get(
        "/api/auth/me", headers=_headers(_login(client, "clerk2@example.uz")["access_token"])
    ).json()["id"]
    app = cast("FastAPI", client.app)

    async def list_as(user_id: str, *, with_access: bool) -> object:
        engine = create_async_engine(app.state.settings.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        ctx = TenantContext(
            tenant_id=UUID(shop_id), actor=Actor(kind="user", id=user_id), request_id=None
        )
        try:
            async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                access = AccessService(TenantService(uow, bus, ctx)) if with_access else None
                service = OrderService(uow, bus, ctx, access=access)
                return await service.list_all(Page(limit=10, offset=0))
        except PermissionDeniedError as exc:
            return exc
        finally:
            await engine.dispose()

    # A member lacks commerce.order:read: the service refuses even without a route.
    assert isinstance(asyncio.run(list_as(clerk_id, with_access=True)), PermissionDeniedError)
    # A user-facing call path that forgot to wire access fails closed.
    assert isinstance(asyncio.run(list_as(clerk_id, with_access=False)), PermissionDeniedError)
