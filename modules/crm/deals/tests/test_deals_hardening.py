"""crm.deals hardening (feature test — moves with the feature): a deleted contact or
company no longer dangles on deals (the bus subscriber clears the link), a link can
be cleared explicitly with ``null`` in PATCH (absent = unchanged), and RBAC is
enforced in the service, not only on the route."""

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.auth.access_service import AccessService
from core.tenants.service import TenantService
from modules.crm.contacts import ContactsService
from modules.crm.deals import DealService
from modules.crm.deals.subscribers import on_company_deleted, on_contact_deleted
from shared.context import Actor, TenantContext
from shared.errors import PermissionDeniedError
from shared.events import EventBus, EventEnvelope, bus
from shared.handler_runtime import HandlerRuntime, reset_handler_runtime, set_handler_runtime
from shared.ids import new_uuid7
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _login, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


def _post(client: TestClient, token: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post(path, headers=_headers(token), json=body)
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _linked_deal(client: TestClient, owner: str) -> dict[str, Any]:
    company = _post(client, owner, "/api/crm/companies", {"name": "Acme"})
    contact = _post(client, owner, "/api/crm/contacts", {"first_name": "Ali"})
    return _post(
        client,
        owner,
        "/api/crm/deals",
        {
            "title": "Big",
            "amount": 100,
            "contact_id": contact["id"],
            "company_id": company["id"],
        },
    )


def _deliver(
    handler: object,
    database_url: str,
    tenant_id: str,
    name: str,
    payload: dict[str, Any],
) -> None:
    ctx = TenantContext(
        tenant_id=UUID(tenant_id), actor=Actor(kind="system", id="test"), request_id=None
    )
    envelope = EventEnvelope(
        event_id=new_uuid7(),
        name=name,
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=UUID(tenant_id),
        actor=ctx.actor,
        payload=payload,
    )

    async def run() -> None:
        engine = create_async_engine(database_url)  # own pool: one per event loop
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                token = set_handler_runtime(HandlerRuntime(uow=uow, ctx=ctx, bus=EventBus()))
                try:
                    await handler(envelope)  # type: ignore[operator]
                finally:
                    reset_handler_runtime(token)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_deleted_contact_and_company_are_unlinked_from_deals(
    crm_deals_client: TestClient,
) -> None:
    client = crm_deals_client
    database_url = cast("FastAPI", client.app).state.settings.database_url
    _u, tenant_id, owner = _owner_with_tenant(client, "unl@example.uz")
    deal = _linked_deal(client, owner)

    gone = client.delete(f"/api/crm/contacts/{deal['contact_id']}", headers=_headers(owner))
    assert gone.status_code == 204
    _deliver(
        on_contact_deleted,
        database_url,
        tenant_id,
        "crm.contact.deleted",
        {"contact_id": deal["contact_id"]},
    )
    gone = client.delete(f"/api/crm/companies/{deal['company_id']}", headers=_headers(owner))
    assert gone.status_code == 204
    _deliver(
        on_company_deleted,
        database_url,
        tenant_id,
        "crm.company.deleted",
        {"company_id": deal["company_id"]},
    )

    after = client.get(f"/api/crm/deals/{deal['id']}", headers=_headers(owner)).json()
    assert after["contact_id"] is None
    assert after["company_id"] is None


def test_patch_null_clears_a_link_absent_keeps_it(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, _tenant_id, owner = _owner_with_tenant(client, "clr@example.uz")
    deal = _linked_deal(client, owner)

    kept = client.patch(
        f"/api/crm/deals/{deal['id']}", headers=_headers(owner), json={"title": "Renamed"}
    ).json()
    assert kept["contact_id"] == deal["contact_id"]

    cleared = client.patch(
        f"/api/crm/deals/{deal['id']}", headers=_headers(owner), json={"contact_id": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["contact_id"] is None
    assert cleared.json()["company_id"] == deal["company_id"]


def test_member_cannot_delete_through_the_service(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, tenant_id, owner = _owner_with_tenant(client, "dsv@example.uz")
    deal = _linked_deal(client, owner)
    _member_of(client, owner, tenant_id, "rep@example.uz")
    rep_token = _login(client, "rep@example.uz")["access_token"]
    rep_id = client.get("/api/auth/me", headers=_headers(rep_token)).json()["id"]
    app = cast("FastAPI", client.app)

    async def delete_as_rep() -> object:
        engine = create_async_engine(app.state.settings.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        ctx = TenantContext(
            tenant_id=UUID(tenant_id), actor=Actor(kind="user", id=rep_id), request_id=None
        )
        try:
            async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                access = AccessService(TenantService(uow, bus, ctx))
                contacts = ContactsService(uow, bus, ctx, access=access)
                deals = DealService(
                    uow, bus, ctx, contacts=contacts, stages=("lead",), access=access
                )
                await deals.delete(UUID(deal["id"]))
                return None
        except PermissionDeniedError as exc:
            return exc
        finally:
            await engine.dispose()

    assert isinstance(asyncio.run(delete_as_rep()), PermissionDeniedError)
    assert client.get(f"/api/crm/deals/{deal['id']}", headers=_headers(owner)).status_code == 200
