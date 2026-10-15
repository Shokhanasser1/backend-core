"""crm.activities end-to-end (feature test — moves with the feature).

Uses the ``crm_deals_client`` fixture (root conftest): ENABLED_MODULES=crm with a
configured funnel and a seeded UZS currency, so an activity can link to a real
contact, company and deal. CRM_ACTIVITY_TYPES is left at its default
(call,meeting,task,email). Covers create/update/complete/reopen/delete, the
filters, link + assignee validation (404), events, the mandatory 403 (a member
cannot delete) and 401, tenant isolation, and the unlink subscribers.

The app is reached only through the fixture — a feature never imports app.*.
"""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from modules.crm.activities.subscribers import (
    on_company_deleted,
    on_contact_deleted,
    on_deal_deleted,
)
from shared.context import Actor, TenantContext
from shared.events import EventBus, EventEnvelope
from shared.handler_runtime import HandlerRuntime, reset_handler_runtime, set_handler_runtime
from shared.ids import new_uuid7
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _login, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration

_API = "/api/crm/activities"


def _post(client: TestClient, token: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post(path, headers=_headers(token), json=body)
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _me(client: TestClient, email: str) -> str:
    token = _login(client, email)["access_token"]
    return cast("str", client.get("/api/auth/me", headers=_headers(token)).json()["id"])


def _iso(moment: datetime) -> str:
    return moment.isoformat()


@contextmanager
def _events(client: TestClient) -> Iterator[list[tuple[str, dict[str, Any]]]]:
    app = cast("FastAPI", client.app)
    captured: list[tuple[str, dict[str, Any]]] = []
    original = app.state.bus.publish

    async def spy(envelope: Any) -> None:
        captured.append((envelope.name, dict(envelope.payload)))
        await original(envelope)

    app.state.bus.publish = spy
    try:
        yield captured
    finally:
        app.state.bus.publish = original


def test_create_defaults_to_first_type_and_creator(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, _t, owner = _owner_with_tenant(client, "act@example.uz")

    body = _post(client, owner, _API, {"subject": "Call Ali"})

    assert body["type"] == "call"  # first of the default CRM_ACTIVITY_TYPES
    assert body["assignee_user_id"] == _me(client, "act@example.uz")
    assert body["done_at"] is None
    assert body["due_at"] is None
    assert (body["contact_id"], body["company_id"], body["deal_id"]) == (None, None, None)


def test_create_with_links_assignee_and_due(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, tenant_id, owner = _owner_with_tenant(client, "lnk@example.uz")
    _member_of(client, owner, tenant_id, "rep@example.uz")
    contact = _post(client, owner, "/api/crm/contacts", {"first_name": "Ali"})
    company = _post(client, owner, "/api/crm/companies", {"name": "Acme"})
    deal = _post(client, owner, "/api/crm/deals", {"title": "Big", "amount": 100})
    due = datetime.now(UTC) + timedelta(days=2)

    body = _post(
        client,
        owner,
        _API,
        {
            "type": "meeting",
            "subject": "Demo",
            "notes": "bring the deck",
            "due_at": _iso(due),
            "assignee_user_id": _me(client, "rep@example.uz"),
            "contact_id": contact["id"],
            "company_id": company["id"],
            "deal_id": deal["id"],
        },
    )

    assert body["type"] == "meeting"
    assert body["assignee_user_id"] == _me(client, "rep@example.uz")
    assert (body["contact_id"], body["company_id"], body["deal_id"]) == (
        contact["id"],
        company["id"],
        deal["id"],
    )
    assert datetime.fromisoformat(body["due_at"]) == due


def test_unknown_type_is_rejected(crm_deals_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_deals_client, "typ@example.uz")
    resp = crm_deals_client.post(
        _API, headers=_headers(owner), json={"subject": "X", "type": "lunch"}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("field", ["contact_id", "company_id", "deal_id", "assignee_user_id"])
def test_foreign_or_missing_reference_is_404(crm_deals_client: TestClient, field: str) -> None:
    client = crm_deals_client
    _u, _t, owner = _owner_with_tenant(client, "own@example.uz")
    _u2, _t2, other = _owner_with_tenant(client, "oth@example.uz")
    makers: dict[str, Callable[[], object]] = {
        "contact_id": lambda: _post(client, other, "/api/crm/contacts", {"first_name": "Z"})["id"],
        "company_id": lambda: _post(client, other, "/api/crm/companies", {"name": "Z"})["id"],
        "deal_id": lambda: _post(client, other, "/api/crm/deals", {"title": "Z", "amount": 1})[
            "id"
        ],
        "assignee_user_id": lambda: _me(client, "oth@example.uz"),  # not a member here
    }
    foreign = str(makers[field]())

    for value in (foreign, str(uuid4())):
        resp = client.post(_API, headers=_headers(owner), json={"subject": "X", field: value})
        assert resp.status_code == 404, (field, resp.text)


def test_complete_and_reopen_are_idempotent(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, _t, owner = _owner_with_tenant(client, "don@example.uz")
    activity = _post(client, owner, _API, {"subject": "Send offer", "type": "task"})
    url = f"{_API}/{activity['id']}"

    with _events(client) as events:
        first = client.post(f"{url}/complete", headers=_headers(owner))
        second = client.post(f"{url}/complete", headers=_headers(owner))
        reopened = client.post(f"{url}/reopen", headers=_headers(owner))
        again = client.post(f"{url}/reopen", headers=_headers(owner))

    assert first.status_code == second.status_code == 200
    assert first.json()["done_at"] is not None
    assert second.json()["done_at"] == first.json()["done_at"]  # unchanged on repeat
    assert reopened.json()["done_at"] is None
    assert again.status_code == 200
    names = [name for name, _ in events]
    assert names.count("crm.activity.completed") == 1
    assert names.count("crm.activity.reopened") == 1


def test_patch_clears_links_and_reassigns(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, tenant_id, owner = _owner_with_tenant(client, "pat@example.uz")
    _member_of(client, owner, tenant_id, "rep2@example.uz")
    deal = _post(client, owner, "/api/crm/deals", {"title": "D", "amount": 1})
    activity = _post(
        client,
        owner,
        _API,
        {
            "subject": "Follow up",
            "deal_id": deal["id"],
            "due_at": _iso(datetime.now(UTC) + timedelta(days=1)),
        },
    )
    url = f"{_API}/{activity['id']}"
    rep_id = _me(client, "rep2@example.uz")

    kept = client.patch(url, headers=_headers(owner), json={"subject": "Follow up!"}).json()
    assert kept["deal_id"] == deal["id"]

    with _events(client) as events:
        cleared = client.patch(
            url,
            headers=_headers(owner),
            json={"deal_id": None, "due_at": None, "assignee_user_id": rep_id},
        )

    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["deal_id"] is None
    assert body["due_at"] is None
    assert body["assignee_user_id"] == rep_id
    assigned = [payload for name, payload in events if name == "crm.activity.assigned"]
    assert assigned == [
        {"activity_id": activity["id"], "from": activity["assignee_user_id"], "to": rep_id}
    ]


def test_list_filters(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, tenant_id, owner = _owner_with_tenant(client, "flt@example.uz")
    member = _member_of(client, owner, tenant_id, "rep3@example.uz")
    rep_id = _me(client, "rep3@example.uz")
    deal = _post(client, owner, "/api/crm/deals", {"title": "D", "amount": 1})
    now = datetime.now(UTC)
    end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=0)

    overdue = _post(
        client, owner, _API, {"subject": "late", "due_at": _iso(now - timedelta(days=2))}
    )
    today = _post(client, owner, _API, {"subject": "today", "due_at": _iso(end_of_today)})
    done = _post(client, owner, _API, {"subject": "done", "type": "email", "deal_id": deal["id"]})
    client.post(f"{_API}/{done['id']}/complete", headers=_headers(owner))
    mine_rep = _post(client, owner, _API, {"subject": "rep's", "assignee_user_id": rep_id})

    def ids(token: str, query: str) -> set[str]:
        resp = client.get(f"{_API}?{query}", headers=_headers(token))
        assert resp.status_code == 200, resp.text
        return {item["id"] for item in resp.json()["items"]}

    assert ids(owner, "overdue=true") == {overdue["id"]}
    assert ids(owner, "due_today=true") == {today["id"]}
    assert ids(owner, "status=done") == {done["id"]}
    assert ids(owner, "status=open") == {overdue["id"], today["id"], mine_rep["id"]}
    assert ids(owner, "type=email") == {done["id"]}
    assert ids(owner, f"deal_id={deal['id']}") == {done["id"]}
    assert ids(member, "mine=true") == {mine_rep["id"]}
    assert ids(owner, f"assignee_user_id={rep_id}") == {mine_rep["id"]}


def test_member_cannot_delete_owner_can(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, tenant_id, owner = _owner_with_tenant(client, "del@example.uz")
    member = _member_of(client, owner, tenant_id, "rep4@example.uz")
    activity = _post(client, owner, _API, {"subject": "X"})
    url = f"{_API}/{activity['id']}"

    assert client.delete(url, headers=_headers(member)).status_code == 403
    # A member works with activities: read/create/update/complete.
    assert client.get(url, headers=_headers(member)).status_code == 200
    assert client.post(f"{url}/complete", headers=_headers(member)).status_code == 200
    assert client.delete(url, headers=_headers(owner)).status_code == 204
    assert client.get(url, headers=_headers(owner)).status_code == 404


def test_requires_authentication(crm_deals_client: TestClient) -> None:
    assert crm_deals_client.get(_API).status_code == 401
    assert crm_deals_client.post(_API, json={"subject": "X"}).status_code == 401


def test_tenant_isolation(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, _t, owner_a = _owner_with_tenant(client, "isa@example.uz")
    _u2, _t2, owner_b = _owner_with_tenant(client, "isb@example.uz")
    activity = _post(client, owner_a, _API, {"subject": "A's"})

    assert client.get(f"{_API}/{activity['id']}", headers=_headers(owner_b)).status_code == 404
    assert client.get(_API, headers=_headers(owner_b)).json()["items"] == []


def _deliver(client: TestClient, handler: Any, tenant_id: str, name: str, payload: dict) -> None:  # type: ignore[type-arg]
    database_url = cast("FastAPI", client.app).state.settings.database_url
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
                    await handler(envelope)
                finally:
                    reset_handler_runtime(token)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_deleted_contact_company_and_deal_are_unlinked(crm_deals_client: TestClient) -> None:
    client = crm_deals_client
    _u, tenant_id, owner = _owner_with_tenant(client, "unl@example.uz")
    contact = _post(client, owner, "/api/crm/contacts", {"first_name": "Ali"})
    company = _post(client, owner, "/api/crm/companies", {"name": "Acme"})
    deal = _post(client, owner, "/api/crm/deals", {"title": "D", "amount": 1})
    activity = _post(
        client,
        owner,
        _API,
        {
            "subject": "Call",
            "contact_id": contact["id"],
            "company_id": company["id"],
            "deal_id": deal["id"],
        },
    )

    for path, handler, name, key, value in (
        ("/api/crm/contacts", on_contact_deleted, "crm.contact.deleted", "contact_id", contact),
        ("/api/crm/companies", on_company_deleted, "crm.company.deleted", "company_id", company),
        ("/api/crm/deals", on_deal_deleted, "crm.deal.deleted", "deal_id", deal),
    ):
        gone = client.delete(f"{path}/{value['id']}", headers=_headers(owner))
        assert gone.status_code == 204, gone.text
        _deliver(client, handler, tenant_id, name, {key: value["id"]})

    after = client.get(f"{_API}/{activity['id']}", headers=_headers(owner)).json()
    assert (after["contact_id"], after["company_id"], after["deal_id"]) == (None, None, None)
    assert after["subject"] == "Call"  # the history itself is kept
