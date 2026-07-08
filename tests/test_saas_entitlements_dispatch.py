"""Reliable delivery of billing.subscription.* to the saas.entitlements subscriber
through the REAL worker path (arq dispatch_event), not just an in-process call.

Proves the production contract for the feature's subscriber:
- the worker resolves the feature handler by id (registered via install(), which
  the worker runs through install_module_workers);
- ``activated`` sets the tenant's active plan; ``canceled`` flags it;
- redelivery (arq is at-least-once) is a no-op — processed_events makes it
  effectively-once per handler.

All dispatches + reads run inside ONE asyncio.run with engines created in that
loop (reusing an engine across the loops asyncio.run spins up would bind a pooled
connection to a closed loop). The bus enqueue is silenced so the handler's own
re-published events don't reach the web app's arq pool (bound to another loop).

Lives in tests/ (not the feature folder): it drives app.worker, and a feature may
not import app (modules -> app layer boundary).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.worker import dispatch_event
from modules.saas.entitlements.models import TenantEntitlement
from shared.context import Actor
from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_USER
from shared.events import EventEnvelope, bus
from tests.test_auth_flows import _owner_with_tenant

pytestmark = pytest.mark.integration

_ACTIVATED = "modules.saas.entitlements.subscribers.on_subscription_activated"
_CANCELED = "modules.saas.entitlements.subscribers.on_subscription_canceled"


def _envelope(name: str, tenant_id: str, payload: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid4(),
        name=name,
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=UUID(tenant_id),
        actor=Actor(kind="system", id="billing"),
        payload=payload,
    )


async def _silent_enqueue(handler_id: str, wire: dict[str, Any]) -> None:
    return None


async def _drive(role_urls: dict[str, str], tenant_id: str) -> list[tuple[str, bool] | None]:
    """Run activation -> redelivery -> cancellation through the real dispatcher and
    return the tenant's (plan_code, canceled) after each, read as app_maintenance."""
    bus.bind_enqueue(_silent_enqueue)
    user_engine = create_async_engine(role_urls[ROLE_USER])
    maint_engine = create_async_engine(role_urls[ROLE_MAINTENANCE])
    maint_factory = async_sessionmaker(maint_engine, expire_on_commit=False)
    worker_ctx: dict[str, Any] = {
        "bus": bus,
        "session_factory": async_sessionmaker(user_engine, expire_on_commit=False),
        "maintenance_sessions": maint_factory,
        "job_try": 1,
    }

    async def _row() -> tuple[str, bool] | None:
        async with maint_factory() as session:  # maintenance: bypasses RLS
            row = (
                await session.execute(
                    select(TenantEntitlement).where(TenantEntitlement.tenant_id == UUID(tenant_id))
                )
            ).scalar_one_or_none()
            return (row.plan_code, row.canceled) if row is not None else None

    try:
        period_end = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        activated = _envelope(
            "billing.subscription.activated",
            tenant_id,
            {"subscription_id": str(uuid4()), "plan_code": "pro", "current_period_end": period_end},
        )
        observed: list[tuple[str, bool] | None] = []
        await dispatch_event(worker_ctx, _ACTIVATED, activated.to_wire())
        observed.append(await _row())
        # Redelivery of the SAME event is a no-op (processed_events dedup).
        await dispatch_event(worker_ctx, _ACTIVATED, activated.to_wire())
        observed.append(await _row())
        # Cancellation flags the row; coverage still holds until the period end.
        canceled = _envelope(
            "billing.subscription.canceled",
            tenant_id,
            {"subscription_id": str(uuid4()), "plan_code": "pro"},
        )
        await dispatch_event(worker_ctx, _CANCELED, canceled.to_wire())
        observed.append(await _row())
        return observed
    finally:
        await user_engine.dispose()
        await maint_engine.dispose()


def test_worker_applies_activation_then_cancellation(
    saas_client: TestClient, role_urls: dict[str, str]
) -> None:
    _u, tenant_id, _owner = _owner_with_tenant(saas_client, "wrk-saas@example.uz")
    observed = asyncio.run(_drive(role_urls, tenant_id))
    assert observed == [("pro", False), ("pro", False), ("pro", True)]
