"""RBAC inside core services (CLAUDE.md: the route is not the only line).

Calls go straight to the services — no route, so no require_permission — as a
tenant MEMBER (holds only read codes) and as a STRANGER (authenticated user, not a
member). Each must be refused by the service itself. A user-facing service built
without an AccessService fails closed. Internal actors (jobs, bus handlers) are
covered by the job/subscriber tests, which keep passing unchanged.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.audit.schemas import AuditQuery
from core.audit.service import AuditService
from core.auth.access_service import AccessService
from core.billing.service import BillingService, PaymentService
from core.files.service import FileService
from core.tenants.service import TenantService
from shared.context import Actor, TenantContext
from shared.errors import PermissionDeniedError
from shared.events import EventBus
from shared.pagination import Page
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import (
    _headers,
    _login,
    _owner_with_tenant,
    _register,
    invitation_token,
)
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

Build = Callable[[Any, EventBus, TenantContext, AccessService | None, FastAPI], Any]
Call = Callable[[Any], Awaitable[object]]


def _tenants(uow: Any, bus: EventBus, ctx: TenantContext, access: Any, app: FastAPI) -> Any:
    return TenantService(uow, bus, ctx)  # resolves its own permissions


def _billing(uow: Any, bus: EventBus, ctx: TenantContext, access: Any, app: FastAPI) -> Any:
    payments = PaymentService(
        uow, bus, ctx, providers={}, settings=app.state.settings, access=access
    )
    return BillingService(uow, bus, ctx, payments=payments, access=access)


def _payments(uow: Any, bus: EventBus, ctx: TenantContext, access: Any, app: FastAPI) -> Any:
    return PaymentService(uow, bus, ctx, providers={}, settings=app.state.settings, access=access)


def _files(uow: Any, bus: EventBus, ctx: TenantContext, access: Any, app: FastAPI) -> Any:
    return FileService(
        uow,
        bus,
        ctx,
        storage=app.state.file_storage,
        thumbnailer=app.state.file_thumbnailer,
        settings=app.state.settings,
        access=access,
    )


def _audit(uow: Any, bus: EventBus, ctx: TenantContext, access: Any, app: FastAPI) -> Any:
    return AuditService(uow.session, ctx, access=access)


def _run(
    client: TestClient, tenant_id: str, user_id: str, build: Build, call: Call, *, wired: bool
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
                access = AccessService(TenantService(uow, bus, ctx)) if wired else None
                return await call(build(uow, bus, ctx, access, app))
        except PermissionDeniedError as exc:
            return exc
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _user_id(client: TestClient, email: str) -> str:
    token = _login(client, email)["access_token"]
    return cast("str", client.get("/api/auth/me", headers=_headers(token)).json()["id"])


@pytest.fixture
def world(client: TestClient) -> dict[str, str]:
    """An owner's tenant with one member (read-only codes) and one stranger."""
    _u, tenant_id, owner = _owner_with_tenant(client, "gowner@example.uz")
    _member_of(client, owner, tenant_id, "gmember@example.uz")
    _register(client, "gstranger@example.uz")
    file_id = client.post(
        "/api/files", headers=_headers(owner), files={"upload": ("a.png", _PNG, "image/png")}
    ).json()["id"]
    member_id = _user_id(client, "gmember@example.uz")
    return {
        "tenant_id": tenant_id,
        "owner": owner,
        "member": member_id,
        "stranger": _user_id(client, "gstranger@example.uz"),
        "file_id": file_id,
    }


# (label, build, call) — writes refused to a MEMBER (who holds only read codes).
MEMBER_DENIED: list[tuple[str, Build, Callable[[dict[str, str]], Call]]] = [
    ("tenants.update", _tenants, lambda w: lambda s: s.update_tenant(name="X")),
    ("tenants.invite", _tenants, lambda w: lambda s: s.invite_member("z@example.uz", "member")),
    ("tenants.revoke", _tenants, lambda w: lambda s: s.revoke_invitation(uuid4())),
    (
        "tenants.role",
        _tenants,
        lambda w: lambda s: s.change_member_role(UUID(w["member"]), "admin"),
    ),
    ("tenants.remove", _tenants, lambda w: lambda s: s.remove_member(UUID(w["member"]))),
    ("billing.start", _billing, lambda w: lambda s: s.start_subscription("pro", "payme")),
    ("billing.cancel", _billing, lambda w: lambda s: s.cancel_subscription()),
    (
        "files.upload",
        _files,
        lambda w: lambda s: s.upload(filename="b.png", declared_content_type=None, data=_PNG),
    ),
    ("files.delete", _files, lambda w: lambda s: s.delete(UUID(w["file_id"]))),
    ("audit.search", _audit, lambda w: lambda s: s.search(AuditQuery(), Page(limit=5, offset=0))),
]

# Reads refused to a STRANGER (authenticated, not a member of the tenant).
STRANGER_DENIED: list[tuple[str, Build, Callable[[dict[str, str]], Call]]] = [
    ("tenants.get", _tenants, lambda w: lambda s: s.get_tenant()),
    ("tenants.members", _tenants, lambda w: lambda s: s.list_members(Page(limit=5, offset=0))),
    ("billing.plans", _billing, lambda w: lambda s: s.list_plans()),
    ("billing.subscription", _billing, lambda w: lambda s: s.get_subscription()),
    ("billing.providers", _payments, lambda w: lambda s: s.list_providers()),
    ("files.meta", _files, lambda w: lambda s: s.get(UUID(w["file_id"]))),
    ("files.open", _files, lambda w: lambda s: s.open(UUID(w["file_id"]))),
]


@pytest.mark.parametrize(
    ("label", "build", "call"), MEMBER_DENIED, ids=[c[0] for c in MEMBER_DENIED]
)
def test_member_is_refused_by_the_service(
    client: TestClient, world: dict[str, str], label: str, build: Build, call: Any
) -> None:
    result = _run(client, world["tenant_id"], world["member"], build, call(world), wired=True)
    assert isinstance(result, PermissionDeniedError), label


@pytest.mark.parametrize(
    ("label", "build", "call"), STRANGER_DENIED, ids=[c[0] for c in STRANGER_DENIED]
)
def test_stranger_is_refused_by_the_service(
    client: TestClient, world: dict[str, str], label: str, build: Build, call: Any
) -> None:
    result = _run(client, world["tenant_id"], world["stranger"], build, call(world), wired=True)
    assert isinstance(result, PermissionDeniedError), label


@pytest.mark.parametrize("build", [_billing, _files, _audit], ids=["billing", "files", "audit"])
def test_unwired_service_fails_closed_for_a_user(
    client: TestClient, world: dict[str, str], build: Build
) -> None:
    calls: dict[Any, Call] = {
        _billing: lambda s: s.list_plans(),
        _files: lambda s: s.get(UUID(world["file_id"])),
        _audit: lambda s: s.search(AuditQuery(), Page(limit=5, offset=0)),
    }
    owner_id = _user_id(client, "gowner@example.uz")
    result = _run(client, world["tenant_id"], owner_id, build, calls[build], wired=False)
    assert isinstance(result, PermissionDeniedError)


def test_member_reads_through_the_service(client: TestClient, world: dict[str, str]) -> None:
    # The guard refuses what the role lacks, never what it holds.
    member = world["member"]
    tenant = _run(
        client, world["tenant_id"], member, _tenants, lambda s: s.get_tenant(), wired=True
    )
    assert getattr(tenant, "id", None) == UUID(world["tenant_id"])
    meta = _run(
        client,
        world["tenant_id"],
        member,
        _files,
        lambda s: s.get(UUID(world["file_id"])),
        wired=True,
    )
    assert getattr(meta, "id", None) == UUID(world["file_id"])


def test_invitation_flow_still_works_end_to_end(client: TestClient) -> None:
    # Regression guard: the owner path through the routes is unaffected.
    _u, _tenant_id, owner = _owner_with_tenant(client, "flow@example.uz")
    resp = client.post(
        "/api/tenants/invitations",
        headers=_headers(owner),
        json={"email": "joiner@example.uz", "role": "member"},
    )
    assert resp.status_code == 201
    assert invitation_token(client, "joiner@example.uz")
