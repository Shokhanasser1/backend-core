"""saas.onboarding hardening (feature test — moves with the feature): concurrent
step completions never surface an IntegrityError, and when the last two steps
finish at the same time ``saas.onboarding.completed`` is still emitted exactly
once (completions are serialized per tenant)."""

import asyncio
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from modules.saas.onboarding.service import OnboardingService
from shared.context import Actor, TenantContext
from shared.events import EventBus, EventEnvelope
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _owner_with_tenant

pytestmark = pytest.mark.integration

STEPS = ("create_shop", "add_product", "connect_payment")


class _CapturingBus(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.names: list[str] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.names.append(event.name)


def _race(client: TestClient, tenant_id: str, steps: list[str]) -> tuple[list[object], list[str]]:
    app = cast("FastAPI", client.app)
    bus = _CapturingBus()
    ctx = TenantContext(
        tenant_id=UUID(tenant_id), actor=Actor(kind="user", id=str(uuid4())), request_id=None
    )

    async def run() -> list[object]:
        engine = create_async_engine(app.state.settings.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def complete(step: str) -> object:
            async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                service = OnboardingService(uow, bus, ctx, steps=STEPS)
                result = await service.complete_step(step)
                await asyncio.sleep(0.2)  # hold the transaction open across the race
                return result

        try:
            return list(await asyncio.gather(*(complete(s) for s in steps), return_exceptions=True))
        finally:
            await engine.dispose()

    return asyncio.run(run()), bus.names


def test_last_two_steps_at_once_complete_exactly_once(
    saas_onboarding_client: TestClient,
) -> None:
    _u, tenant_id, _owner = _owner_with_tenant(saas_onboarding_client, "onb@example.uz")
    _race(saas_onboarding_client, tenant_id, ["create_shop"])

    results, events = _race(saas_onboarding_client, tenant_id, ["add_product", "connect_payment"])

    assert not [r for r in results if isinstance(r, BaseException)]
    assert events.count("saas.onboarding.completed") == 1


def test_same_step_at_once_is_idempotent(saas_onboarding_client: TestClient) -> None:
    _u, tenant_id, _owner = _owner_with_tenant(saas_onboarding_client, "onb2@example.uz")

    results, _events = _race(saas_onboarding_client, tenant_id, ["create_shop", "create_shop"])

    assert not [r for r in results if isinstance(r, BaseException)]  # no IntegrityError
