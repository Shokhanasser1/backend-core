"""saas.metering retention through the REAL worker job (app.worker.purge_retention).

Proves two things the feature promises:
- old usage buckets past SAAS_USAGE_RETENTION_DAYS are swept (as app_maintenance);
- the sweep is wired into the worker's daily retention job, but only when the saas
  module is enabled (lazy import guarded by ENABLED_MODULES).

Lives in tests/ (not the feature folder): it drives app.worker, and a feature may
not import app (modules -> app layer boundary). Engines are created inside the one
asyncio.run so a pooled connection never crosses a closed loop.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.worker import purge_retention
from modules.saas.metering.models import UsageCounter
from modules.saas.metering.service import MeteringService
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_RETENTION, ROLE_USER
from shared.events import bus
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _owner_with_tenant

pytestmark = pytest.mark.integration


async def _seed_and_sweep(
    role_urls: dict[str, str], settings: Settings, tenant_id: str
) -> tuple[dict[str, int], int]:
    user_engine = create_async_engine(role_urls[ROLE_USER])
    maint_engine = create_async_engine(role_urls[ROLE_MAINTENANCE])
    retention_engine = create_async_engine(role_urls[ROLE_RETENTION])
    user_factory = async_sessionmaker(user_engine, expire_on_commit=False)
    maint_factory = async_sessionmaker(maint_engine, expire_on_commit=False)
    retention_factory = async_sessionmaker(retention_engine, expire_on_commit=False)
    try:
        ctx = TenantContext(
            tenant_id=UUID(tenant_id), actor=Actor(kind="user", id=str(uuid4())), request_id=None
        )
        now = datetime.now(UTC)
        async with SqlAlchemyUnitOfWork(user_factory, context=ctx) as uow:
            service = MeteringService(uow, bus, ctx)
            await service.record("commerce.order", 5, at=now - timedelta(days=500))  # expired
            await service.record("commerce.order", 2, at=now)  # recent

        worker_ctx: dict[str, Any] = {
            "settings": settings,
            "retention_sessions": retention_factory,
            "maintenance_sessions": maint_factory,
        }
        counts = await purge_retention(worker_ctx)

        async with maint_factory() as session:  # maintenance: bypasses RLS
            remaining = (
                await session.execute(select(func.count()).select_from(UsageCounter))
            ).scalar_one()
        return counts, remaining
    finally:
        await user_engine.dispose()
        await maint_engine.dispose()
        await retention_engine.dispose()


def test_worker_sweeps_expired_usage_when_saas_enabled(
    saas_client: TestClient, test_settings: Settings, role_urls: dict[str, str]
) -> None:
    _u, tenant_id, _owner = _owner_with_tenant(saas_client, "ret-saas@example.uz")
    settings = test_settings.model_copy(update={"enabled_modules": "saas"})
    counts, remaining = asyncio.run(_seed_and_sweep(role_urls, settings, tenant_id))

    # The daily job reports the feature's sweep only when saas is enabled...
    assert counts["saas_usage_counters"] == 1  # the 500-day-old bucket
    # ...and the recent bucket survives (one row left).
    assert remaining == 1
