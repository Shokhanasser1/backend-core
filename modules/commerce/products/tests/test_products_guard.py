"""commerce.products: RBAC is enforced in the service too (the route is not the
only line). Staff methods require the permission for a user actor; the sibling
pricing interface (get_sale_info) stays open to storefront buyers."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.auth.access_service import AccessService
from core.tenants.service import TenantService
from modules.commerce.products import ProductService
from shared.context import Actor, TenantContext
from shared.errors import PermissionDeniedError
from shared.events import EventBus
from shared.money import Money
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _login, _owner_with_tenant, _register
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration

Call = Callable[[ProductService], Awaitable[object]]


def _run(client: TestClient, shop_id: str, actor: Actor, call: Call) -> object:
    app = cast("FastAPI", client.app)

    async def go() -> object:
        engine = create_async_engine(app.state.settings.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        ctx = TenantContext(tenant_id=UUID(shop_id), actor=actor, request_id=None)
        bus = EventBus()
        try:
            async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                access = AccessService(TenantService(uow, bus, ctx))
                return await call(ProductService(uow, bus, ctx, access=access))
        except PermissionDeniedError as exc:
            return exc
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _user_id(client: TestClient, email: str) -> str:
    token = _login(client, email)["access_token"]
    return cast("str", client.get("/api/auth/me", headers=_headers(token)).json()["id"])


def test_member_cannot_create_through_the_service(commerce_client: TestClient) -> None:
    _u, shop_id, owner = _owner_with_tenant(commerce_client, "pg@example.uz")
    _member_of(commerce_client, owner, shop_id, "pgclerk@example.uz")
    clerk = Actor(kind="user", id=_user_id(commerce_client, "pgclerk@example.uz"))

    result = _run(
        commerce_client,
        shop_id,
        clerk,
        lambda s: s.create(sku="X1", name="Nope", price_amount=1),
    )

    assert isinstance(result, PermissionDeniedError)


def test_owner_creates_and_buyer_prices_through_the_service(commerce_client: TestClient) -> None:
    _u, shop_id, _owner = _owner_with_tenant(commerce_client, "pg2@example.uz")
    owner = Actor(kind="user", id=_user_id(commerce_client, "pg2@example.uz"))
    created = _run(
        commerce_client, shop_id, owner, lambda s: s.create(sku="X2", name="Ok", price_amount=9)
    )
    product_id = created.id  # type: ignore[attr-defined]

    _register(commerce_client, "pgbuyer@example.uz")
    buyer = Actor(kind="user", id=_user_id(commerce_client, "pgbuyer@example.uz"))
    price = _run(commerce_client, shop_id, buyer, lambda s: s.get_sale_info(product_id))

    assert price == Money(9, "UZS")  # storefront pricing needs no staff permission
