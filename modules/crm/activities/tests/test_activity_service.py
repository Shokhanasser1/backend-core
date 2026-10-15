"""ActivityService at the service level (feature test — moves with the feature):
an empty CRM_ACTIVITY_TYPES makes the feature unusable (422, not a silent
default), and RBAC holds inside the service, not only on the route."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.auth.access_service import AccessService
from core.tenants.service import TenantService
from modules.crm.activities import ActivityService, CreateActivityIn
from modules.crm.contacts import ContactsService
from modules.crm.deals import DealService
from shared.context import Actor, TenantContext
from shared.errors import InvariantViolationError, PermissionDeniedError
from shared.events import EventBus
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _login, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


def _run(
    client: TestClient,
    tenant_id: str,
    user_id: str,
    call: Callable[[ActivityService], Awaitable[object]],
    *,
    types: tuple[str, ...] = ("call", "meeting", "task", "email"),
) -> object:
    app = cast("FastAPI", client.app)

    async def go() -> object:
        engine = create_async_engine(app.state.settings.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        ctx = TenantContext(
            tenant_id=UUID(tenant_id), actor=Actor(kind="user", id=user_id), request_id=None
        )
        bus = EventBus()
        try:
            async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                access = AccessService(TenantService(uow, bus, ctx))
                contacts = ContactsService(uow, bus, ctx)
                deals = DealService(uow, bus, ctx, contacts=contacts, stages=())
                service = ActivityService(
                    uow, bus, ctx, contacts=contacts, deals=deals, types=types, access=access
                )
                return await call(service)
        except (InvariantViolationError, PermissionDeniedError) as exc:
            return exc
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _me(client: TestClient, email: str) -> str:
    token = _login(client, email)["access_token"]
    return cast("str", client.get("/api/auth/me", headers=_headers(token)).json()["id"])


def test_empty_type_configuration_is_unusable(crm_deals_client: TestClient) -> None:
    _u, tenant_id, _owner = _owner_with_tenant(crm_deals_client, "emp@example.uz")
    owner_id = _me(crm_deals_client, "emp@example.uz")

    result = _run(
        crm_deals_client,
        tenant_id,
        owner_id,
        lambda s: s.create(CreateActivityIn(subject="X")),
        types=(),
    )

    assert isinstance(result, InvariantViolationError)


def test_member_cannot_delete_through_the_service(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, tenant_id, owner = _owner_with_tenant(client, "sgd@example.uz")
    _member_of(client, owner, tenant_id, "sgdrep@example.uz")
    activity: dict[str, Any] = client.post(
        "/api/crm/activities", headers=_headers(owner), json={"subject": "Keep me"}
    ).json()
    rep_id = _me(client, "sgdrep@example.uz")

    result = _run(client, tenant_id, rep_id, lambda s: s.delete(UUID(activity["id"])))

    assert isinstance(result, PermissionDeniedError)
    still = client.get(f"/api/crm/activities/{activity['id']}", headers=_headers(owner))
    assert still.status_code == 200
