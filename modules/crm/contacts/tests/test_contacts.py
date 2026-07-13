"""crm.contacts address-book end-to-end (feature test — moves with the feature).

Uses the ``crm_client`` fixture (root conftest): a running app with
ENABLED_MODULES=crm, so the loader has mounted the router and registered the RBAC.
Exercises CRUD for companies and contacts, the company link and its SET-NULL on
delete, the mandatory 403 (a member cannot delete) and 401 (no token), and tenant
isolation.

The app is reached only through the fixture — a feature never imports app.* (that
would cross the modules -> app layer boundary; import-linter enforces it).
"""

import pytest
from fastapi.testclient import TestClient

from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


def test_owner_manages_company_and_contact(crm_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_client, "crmowner@example.uz")

    company = crm_client.post(
        "/api/crm/companies",
        headers=_headers(owner),
        json={"name": "Acme LLC", "website": "acme.uz", "industry": "retail"},
    )
    assert company.status_code == 201, company.text
    company_id = company.json()["id"]

    contact = crm_client.post(
        "/api/crm/contacts",
        headers=_headers(owner),
        json={
            "first_name": "Ali",
            "last_name": "Valiev",
            "email": "ali@acme.uz",
            "position": "CEO",
            "company_id": company_id,
        },
    )
    assert contact.status_code == 201, contact.text
    body = contact.json()
    assert body["company_id"] == company_id
    assert body["first_name"] == "Ali"

    # Listing + filtering by company.
    listed = crm_client.get(
        "/api/crm/contacts", headers=_headers(owner), params={"company_id": company_id}
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    companies = crm_client.get("/api/crm/companies", headers=_headers(owner))
    assert companies.json()["total"] == 1


def test_update_contact_and_reassign_company(crm_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_client, "crmupd@example.uz")
    c1 = crm_client.post(
        "/api/crm/companies", headers=_headers(owner), json={"name": "First"}
    ).json()["id"]
    c2 = crm_client.post(
        "/api/crm/companies", headers=_headers(owner), json={"name": "Second"}
    ).json()["id"]
    contact_id = crm_client.post(
        "/api/crm/contacts",
        headers=_headers(owner),
        json={"first_name": "Bek", "company_id": c1},
    ).json()["id"]

    updated = crm_client.patch(
        f"/api/crm/contacts/{contact_id}",
        headers=_headers(owner),
        json={
            "first_name": "Bekzod",
            "last_name": "Karimov",
            "email": "bek@second.uz",
            "phone": "+998901112233",
            "position": "Manager",
            "notes": "key account",
            "company_id": c2,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["first_name"] == "Bekzod"
    assert body["position"] == "Manager"
    assert body["last_name"] == "Karimov"
    assert body["email"] == "bek@second.uz"
    assert body["phone"] == "+998901112233"
    assert body["notes"] == "key account"
    assert body["company_id"] == c2


def test_update_company_and_delete_contact(crm_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_client, "crmedit@example.uz")
    company_id = crm_client.post(
        "/api/crm/companies", headers=_headers(owner), json={"name": "Old Name"}
    ).json()["id"]

    updated = crm_client.patch(
        f"/api/crm/companies/{company_id}",
        headers=_headers(owner),
        json={"name": "New Name", "website": "new.uz", "industry": "it", "notes": "vip"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "New Name"
    assert body["website"] == "new.uz"
    assert body["industry"] == "it"
    assert body["notes"] == "vip"

    # Owner deletes a contact (success path -> 204, then gone).
    contact_id = crm_client.post(
        "/api/crm/contacts", headers=_headers(owner), json={"first_name": "Temp"}
    ).json()["id"]
    assert (
        crm_client.delete(f"/api/crm/contacts/{contact_id}", headers=_headers(owner)).status_code
        == 204
    )
    assert (
        crm_client.get(f"/api/crm/contacts/{contact_id}", headers=_headers(owner)).status_code
        == 404
    )


def test_unknown_company_on_contact_is_404(crm_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_client, "crm404@example.uz")
    # A random (non-existent) company id -> NotFound at the service boundary.
    ghost = "00000000-0000-0000-0000-000000000000"
    resp = crm_client.post(
        "/api/crm/contacts",
        headers=_headers(owner),
        json={"first_name": "Nobody", "company_id": ghost},
    )
    assert resp.status_code == 404


def test_delete_company_unassigns_its_contacts(crm_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(crm_client, "crmdel@example.uz")
    company_id = crm_client.post(
        "/api/crm/companies", headers=_headers(owner), json={"name": "Doomed"}
    ).json()["id"]
    contact_id = crm_client.post(
        "/api/crm/contacts",
        headers=_headers(owner),
        json={"first_name": "Orphan", "company_id": company_id},
    ).json()["id"]

    deleted = crm_client.delete(f"/api/crm/companies/{company_id}", headers=_headers(owner))
    assert deleted.status_code == 204
    # Company is gone...
    assert (
        crm_client.get(f"/api/crm/companies/{company_id}", headers=_headers(owner)).status_code
        == 404
    )
    # ...but the contact survives with company_id cleared (FK ON DELETE SET NULL).
    contact = crm_client.get(f"/api/crm/contacts/{contact_id}", headers=_headers(owner))
    assert contact.status_code == 200
    assert contact.json()["company_id"] is None


def test_member_works_but_cannot_delete(crm_client: TestClient) -> None:
    _u, tenant_id, owner = _owner_with_tenant(crm_client, "crmboss@example.uz")
    member = _member_of(crm_client, owner, tenant_id, "crmrep@example.uz")

    # Member holds crm.contact:create -> can create.
    created = crm_client.post(
        "/api/crm/contacts", headers=_headers(member), json={"first_name": "Lead"}
    )
    assert created.status_code == 201
    contact_id = created.json()["id"]

    # Member lacks crm.contact:delete -> 403 (DoD negative test).
    blocked = crm_client.delete(f"/api/crm/contacts/{contact_id}", headers=_headers(member))
    assert blocked.status_code == 403


def test_requires_authentication(crm_client: TestClient) -> None:
    # No bearer token -> 401 (DoD negative test).
    assert crm_client.get("/api/crm/contacts").status_code == 401
    assert crm_client.get("/api/crm/companies").status_code == 401


def test_tenant_isolation(crm_client: TestClient) -> None:
    # Distinct email[:3] prefixes: _owner_with_tenant derives the tenant slug from
    # them, so same-prefix emails would collide (409) before crm is even exercised.
    _ua, _ta, owner_a = _owner_with_tenant(crm_client, "alfa@example.uz")
    _ub, _tb, owner_b = _owner_with_tenant(crm_client, "beta@example.uz")
    contact_id = crm_client.post(
        "/api/crm/contacts",
        headers=_headers(owner_a),
        json={"first_name": "Secret"},
    ).json()["id"]

    # Tenant B sees neither A's contact by id (404, not 403) nor in the list.
    assert (
        crm_client.get(f"/api/crm/contacts/{contact_id}", headers=_headers(owner_b)).status_code
        == 404
    )
    assert crm_client.get("/api/crm/contacts", headers=_headers(owner_b)).json()["total"] == 0
