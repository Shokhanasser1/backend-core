"""saas.onboarding end-to-end (feature test — moves with the feature).

Uses the ``saas_onboarding_client`` fixture (ENABLED_MODULES=saas with a configured
checklist create_shop,add_product,connect_payment). Everything is exercised over
HTTP — the /me overview, POST step completion (idempotent), the completed roll-up,
the unknown-step 422, the mandatory 403/401 and tenant isolation — so no direct
service wiring is needed (completing the last step runs the completion-event path
inside the app's own loop).

The app is reached only through the fixture — a feature never imports app.* (that
would cross the modules -> app layer boundary; import-linter enforces it).
"""

from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from tests.test_auth_flows import _headers, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration

_STEPS = ["create_shop", "add_product", "connect_payment"]


def _complete(client: TestClient, token: str, step: str) -> Response:
    return cast(
        "Response",
        client.post(f"/api/saas/onboarding/steps/{step}/complete", headers=_headers(token)),
    )


def test_progress_starts_all_pending(saas_onboarding_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(saas_onboarding_client, "obr@example.uz")
    resp = saas_onboarding_client.get("/api/saas/onboarding/me", headers=_headers(owner))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["key"] for s in body["steps"]] == _STEPS  # configured order preserved
    assert all(not s["completed"] for s in body["steps"])
    assert body == {
        "steps": [{"key": k, "completed": False, "completed_at": None} for k in _STEPS],
        "completed_count": 0,
        "total": 3,
        "is_complete": False,
    }


def test_complete_step_and_finish(saas_onboarding_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(saas_onboarding_client, "fin@example.uz")

    first = _complete(saas_onboarding_client, owner, "create_shop")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["completed_count"] == 1
    assert body["is_complete"] is False
    step = next(s for s in body["steps"] if s["key"] == "create_shop")
    assert step["completed"] is True and step["completed_at"] is not None

    # Completing every configured step flips is_complete (runs the completed-event path).
    _complete(saas_onboarding_client, owner, "add_product")
    last = _complete(saas_onboarding_client, owner, "connect_payment")
    assert last.json()["completed_count"] == 3
    assert last.json()["is_complete"] is True


def test_complete_is_idempotent(saas_onboarding_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(saas_onboarding_client, "idem@example.uz")
    assert _complete(saas_onboarding_client, owner, "create_shop").status_code == 200
    again = _complete(saas_onboarding_client, owner, "create_shop")
    assert again.status_code == 200
    assert again.json()["completed_count"] == 1  # not doubled


def test_unknown_step_rejected(saas_onboarding_client: TestClient) -> None:
    _u, _t, owner = _owner_with_tenant(saas_onboarding_client, "unk@example.uz")
    # A step outside the configured checklist -> 422 (invariant violation).
    assert _complete(saas_onboarding_client, owner, "nope").status_code == 422


def test_tenant_isolation(saas_onboarding_client: TestClient) -> None:
    _ua, _ta, owner_a = _owner_with_tenant(saas_onboarding_client, "alfa@example.uz")
    _ub, _tb, owner_b = _owner_with_tenant(saas_onboarding_client, "beta@example.uz")
    assert _complete(saas_onboarding_client, owner_a, "create_shop").status_code == 200

    # B's checklist is untouched by A's progress (RLS scopes the rows).
    b_progress = saas_onboarding_client.get(
        "/api/saas/onboarding/me", headers=_headers(owner_b)
    ).json()
    assert b_progress["completed_count"] == 0
    assert b_progress["is_complete"] is False


def test_routes_require_permission(saas_onboarding_client: TestClient) -> None:
    _u, tenant_id, owner = _owner_with_tenant(saas_onboarding_client, "boss@example.uz")
    member = _member_of(saas_onboarding_client, owner, tenant_id, "clerk@example.uz")
    # Member lacks saas.onboarding:read / :update -> 403 (DoD negative test).
    assert (
        saas_onboarding_client.get("/api/saas/onboarding/me", headers=_headers(member)).status_code
        == 403
    )
    assert _complete(saas_onboarding_client, member, "create_shop").status_code == 403
    # No token -> 401.
    assert saas_onboarding_client.get("/api/saas/onboarding/me").status_code == 401
