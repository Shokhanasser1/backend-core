"""saas read overviews are RBAC-checked in the service too (not only on /me):
a tenant member without the saas read codes is refused by EntitlementService,
MeteringService and OnboardingService directly. The sibling APIs (limits, record,
complete_step) stay open — they are called by other features and glue code."""

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
from modules.saas.entitlements import EntitlementService
from modules.saas.metering import MeteringService
from modules.saas.onboarding import OnboardingService
from shared.context import Actor, TenantContext
from shared.errors import PermissionDeniedError
from shared.events import EventBus
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _login, _owner_with_tenant
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration

Build = Callable[[Any, EventBus, TenantContext, AccessService], Any]


def _run(client: TestClient, tenant_id: str, user_id: str, build: Build, call: Any) -> object:
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
                result: Awaitable[object] = call(build(uow, bus, ctx, access))
                return await result
        except PermissionDeniedError as exc:
            return exc
        finally:
            await engine.dispose()

    return asyncio.run(go())


CASES: list[tuple[str, Build, Any]] = [
    (
        "entitlements.snapshot",
        lambda uow, bus, ctx, access: EntitlementService(uow, bus, ctx, access=access),
        lambda s: s.snapshot(),
    ),
    (
        "metering.summary",
        lambda uow, bus, ctx, access: MeteringService(uow, bus, ctx, access=access),
        lambda s: s.summary(),
    ),
    (
        "onboarding.progress",
        lambda uow, bus, ctx, access: OnboardingService(uow, bus, ctx, steps=("a",), access=access),
        lambda s: s.progress(),
    ),
]


@pytest.mark.parametrize(("label", "build", "call"), CASES, ids=[c[0] for c in CASES])
def test_member_is_refused_by_the_saas_service(
    saas_client: TestClient, label: str, build: Build, call: Any
) -> None:
    _u, tenant_id, owner = _owner_with_tenant(saas_client, "sowner@example.uz")
    _member_of(saas_client, owner, tenant_id, "smember@example.uz")
    token = _login(saas_client, "smember@example.uz")["access_token"]
    member_id = saas_client.get("/api/auth/me", headers=_headers(token)).json()["id"]

    result = _run(saas_client, tenant_id, member_id, build, call)

    assert isinstance(result, PermissionDeniedError), label
