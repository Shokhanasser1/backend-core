"""TenantDirectory.is_active_member — the public membership check sibling features
use (e.g. crm.activities validates an assignee) without reading memberships."""

import asyncio
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.tenants.directory import TenantDirectory
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MIGRATOR
from shared.service import SqlAlchemyUnitOfWork
from tests.test_auth_flows import _headers, _login, _owner_with_tenant, _register
from tests.test_auth_flows_extended import _member_of

pytestmark = pytest.mark.integration


def _user_id(client: TestClient, email: str) -> str:
    token = _login(client, email)["access_token"]
    return cast("str", client.get("/api/auth/me", headers=_headers(token)).json()["id"])


def _is_member(client: TestClient, tenant_id: str, user_id: str) -> bool:
    app = cast("FastAPI", client.app)

    async def go() -> bool:
        engine = create_async_engine(app.state.settings.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        ctx = TenantContext(
            tenant_id=UUID(tenant_id), actor=Actor(kind="user", id=str(uuid4())), request_id=None
        )
        try:
            async with SqlAlchemyUnitOfWork(sessions, context=ctx) as uow:
                return await TenantDirectory(uow.session).is_active_member(
                    UUID(tenant_id), UUID(user_id)
                )
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_active_member_of_active_tenant(client: TestClient, role_urls: dict[str, str]) -> None:
    _u, tenant_id, owner = _owner_with_tenant(client, "dirowner@example.uz")
    _member_of(client, owner, tenant_id, "dirmember@example.uz")
    _register(client, "dirstranger@example.uz")
    owner_id = _user_id(client, "dirowner@example.uz")
    member_id = _user_id(client, "dirmember@example.uz")

    assert _is_member(client, tenant_id, owner_id) is True
    assert _is_member(client, tenant_id, member_id) is True
    assert _is_member(client, tenant_id, _user_id(client, "dirstranger@example.uz")) is False
    assert _is_member(client, tenant_id, str(uuid4())) is False

    async def suspend() -> None:
        engine = create_async_engine(role_urls[ROLE_MIGRATOR])
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE tenants SET status = 'suspended' WHERE id = :id"),
                    {"id": UUID(tenant_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(suspend())
    assert _is_member(client, tenant_id, member_id) is False  # a suspended tenant has none
