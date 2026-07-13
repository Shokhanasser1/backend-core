"""crm.deals pipeline end-to-end (feature test — moves with the feature).

Uses the ``crm_deals_client`` fixture (root conftest): a running app with
ENABLED_MODULES=crm, a configured funnel (CRM_DEAL_STAGES) and a seeded UZS
currency. Enabling crm also mounts crm.contacts, so a deal can link to a real
contact/company. Exercises CRUD, the configurable stage (default + validation),
cross-feature link validation, the mandatory 403 (a member cannot delete) and 401
(no token), and tenant isolation.

The app is reached only through the fixture — a feature never imports app.* (that
would cross the modules -> app layer boundary; import-linter enforces it).
"""

import pytest
from fastapi.testclient import TestClient

from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration

_STAGES = ["lead", "qualified", "proposal", "won", "lost"]


def _new_contact(client: TestClient, token: str, first_name: str = "Lead") -> str:
    resp = client.post(
        "/api/crm/contacts", headers=_headers(token), json={"first_name": first_name}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def test_create_deal_defaults_to_first_stage(crm_deals_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_deals_client, "dealowner@example.uz")
    created = crm_deals_client.post(
        "/api/crm/deals",
        headers=_headers(owner),
        json={"title": "Big deal", "amount": 5000000},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["stage"] == _STAGES[0]  # lead
    assert body["amount"] == 5000000
    assert body["currency"] == "UZS"
    assert body["contact_id"] is None


def test_create_deal_with_stage_and_links(crm_deals_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_deals_client, "deallink@example.uz")
    contact_id = _new_contact(crm_deals_client, owner, "Ali")
    company_id = crm_deals_client.post(
        "/api/crm/companies", headers=_headers(owner), json={"name": "Acme"}
    ).json()["id"]

    created = crm_deals_client.post(
        "/api/crm/deals",
        headers=_headers(owner),
        json={
            "title": "Qualified deal",
            "amount": 1000,
            "stage": "qualified",
            "contact_id": contact_id,
            "company_id": company_id,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["stage"] == "qualified"
    assert body["contact_id"] == contact_id
    assert body["company_id"] == company_id


def test_unknown_stage_rejected(crm_deals_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_deals_client, "dealstage@example.uz")
    resp = crm_deals_client.post(
        "/api/crm/deals",
        headers=_headers(owner),
        json={"title": "X", "amount": 1, "stage": "nonsense"},
    )
    assert resp.status_code == 422  # stage outside CRM_DEAL_STAGES


def test_unknown_contact_link_is_404(crm_deals_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_deals_client, "deal404@example.uz")
    ghost = "00000000-0000-0000-0000-000000000000"
    resp = crm_deals_client.post(
        "/api/crm/deals",
        headers=_headers(owner),
        json={"title": "X", "amount": 1, "contact_id": ghost},
    )
    assert resp.status_code == 404  # contact validated via ContactsService


def test_update_moves_stage_and_edits(crm_deals_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_deals_client, "dealupd@example.uz")
    contact_id = _new_contact(crm_deals_client, owner)
    company_id = crm_deals_client.post(
        "/api/crm/companies", headers=_headers(owner), json={"name": "Buyer LLC"}
    ).json()["id"]
    deal_id = crm_deals_client.post(
        "/api/crm/deals",
        headers=_headers(owner),
        json={"title": "Move me", "amount": 10},
    ).json()["id"]

    updated = crm_deals_client.patch(
        f"/api/crm/deals/{deal_id}",
        headers=_headers(owner),
        json={
            "title": "Moved",
            "amount": 999,
            "currency": "UZS",
            "stage": "won",
            "contact_id": contact_id,
            "company_id": company_id,
            "notes": "closing",
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["title"] == "Moved"
    assert body["amount"] == 999
    assert body["stage"] == "won"
    assert body["contact_id"] == contact_id
    assert body["company_id"] == company_id
    assert body["notes"] == "closing"


def test_list_filters(crm_deals_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_deals_client, "deallist@example.uz")
    contact_id = _new_contact(crm_deals_client, owner)
    company_id = crm_deals_client.post(
        "/api/crm/companies", headers=_headers(owner), json={"name": "Filt Co"}
    ).json()["id"]
    # Two 'lead' deals (one linked to the contact+company) and one 'won'.
    crm_deals_client.post(
        "/api/crm/deals",
        headers=_headers(owner),
        json={
            "title": "a",
            "amount": 1,
            "stage": "lead",
            "contact_id": contact_id,
            "company_id": company_id,
        },
    )
    crm_deals_client.post(
        "/api/crm/deals", headers=_headers(owner), json={"title": "b", "amount": 1, "stage": "lead"}
    )
    crm_deals_client.post(
        "/api/crm/deals", headers=_headers(owner), json={"title": "c", "amount": 1, "stage": "won"}
    )

    assert crm_deals_client.get("/api/crm/deals", headers=_headers(owner)).json()["total"] == 3
    assert (
        crm_deals_client.get(
            "/api/crm/deals", headers=_headers(owner), params={"stage": "lead"}
        ).json()["total"]
        == 2
    )
    assert (
        crm_deals_client.get(
            "/api/crm/deals", headers=_headers(owner), params={"contact_id": contact_id}
        ).json()["total"]
        == 1
    )
    assert (
        crm_deals_client.get(
            "/api/crm/deals", headers=_headers(owner), params={"company_id": company_id}
        ).json()["total"]
        == 1
    )


def test_no_stages_configured_rejected(crm_deals_no_stages_client: TestClient) -> None:
    # Client enabled crm.deals but left CRM_DEAL_STAGES empty -> create without a
    # stage fails cleanly (422), not 500.
    _u, _t, owner = _owner_with_tenant(crm_deals_no_stages_client, "nostage@example.uz")
    resp = crm_deals_no_stages_client.post(
        "/api/crm/deals", headers=_headers(owner), json={"title": "X", "amount": 1}
    )
    assert resp.status_code == 422


def test_member_works_but_cannot_delete(crm_deals_client: TestClient) -> None:
    _u, tenant_id, owner = _owner_with_tenant(crm_deals_client, "dealboss@example.uz")
    member = _member_of(crm_deals_client, owner, tenant_id, "dealrep@example.uz")

    # Member holds crm.deal:create -> can create.
    created = crm_deals_client.post(
        "/api/crm/deals", headers=_headers(member), json={"title": "Reps deal", "amount": 1}
    )
    assert created.status_code == 201
    deal_id = created.json()["id"]

    # Member lacks crm.deal:delete -> 403 (DoD negative test).
    blocked = crm_deals_client.delete(f"/api/crm/deals/{deal_id}", headers=_headers(member))
    assert blocked.status_code == 403

    # Owner can delete (success path -> 204, then gone).
    assert (
        crm_deals_client.delete(f"/api/crm/deals/{deal_id}", headers=_headers(owner)).status_code
        == 204
    )
    assert (
        crm_deals_client.get(f"/api/crm/deals/{deal_id}", headers=_headers(owner)).status_code
        == 404
    )


def test_requires_authentication(crm_deals_client: TestClient) -> None:
    assert crm_deals_client.get("/api/crm/deals").status_code == 401


def test_tenant_isolation(crm_deals_client: TestClient) -> None:
    # Distinct email[:3] prefixes so the derived tenant slugs don't collide.
    _ua, _ta, owner_a = _owner_with_tenant(crm_deals_client, "gamma@example.uz")
    _ub, _tb, owner_b = _owner_with_tenant(crm_deals_client, "delta@example.uz")
    deal_id = crm_deals_client.post(
        "/api/crm/deals",
        headers=_headers(owner_a),
        json={"title": "Secret deal", "amount": 1},
    ).json()["id"]

    assert (
        crm_deals_client.get(f"/api/crm/deals/{deal_id}", headers=_headers(owner_b)).status_code
        == 404
    )
    assert crm_deals_client.get("/api/crm/deals", headers=_headers(owner_b)).json()["total"] == 0
