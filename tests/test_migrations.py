"""Multi-branch Alembic: upgrade heads applies every branch, downgrade is
reversible, "no unapplied heads after upgrade" (update strategy §3.5).

Runs against a throwaway database migrated by app_migrator, exactly as a real
deployment would (schema §3.1); the base migration's role-presence check is
exercised because the roles are provisioned by the role_urls fixture."""

import asyncio
import re

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from migrations.cli import build_config
from migrations.discovery import discover_version_locations
from shared.db_provisioning import ROLE_MIGRATOR

pytestmark = pytest.mark.integration

MIGRATIONS_DB = "migrations_check"


async def _prepare_database(superuser_url: str, migrator_url: str) -> str:
    engine = create_async_engine(superuser_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text(f"DROP DATABASE IF EXISTS {MIGRATIONS_DB}"))
        await connection.execute(text(f"CREATE DATABASE {MIGRATIONS_DB}"))
    await engine.dispose()

    migrations_superuser = re.sub(r"/[^/?]+$", f"/{MIGRATIONS_DB}", superuser_url)
    # Grant schema privileges in the fresh DB (PG16 locks public down).
    grant_engine = create_async_engine(migrations_superuser, isolation_level="AUTOCOMMIT")
    async with grant_engine.connect() as connection:
        await connection.execute(text("GRANT CREATE, USAGE ON SCHEMA public TO app_migrator"))
        await connection.execute(
            text("GRANT USAGE ON SCHEMA public TO app_user, app_maintenance, app_retention")
        )
    await grant_engine.dispose()

    return re.sub(r"/[^/?]+$", f"/{MIGRATIONS_DB}", migrator_url)


async def _fetch_scalars(url: str, query: str) -> list[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(query))
            return [str(value) for value in result.scalars().all()]
    finally:
        await engine.dispose()


async def _table_exists(url: str, table: str) -> bool:
    rows = await _fetch_scalars(
        url,
        f"SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = '{table}'",
    )
    return bool(rows)


def test_upgrade_heads_downgrade_upgrade(
    superuser_url: str, role_urls: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    migrator_url = asyncio.run(_prepare_database(superuser_url, role_urls[ROLE_MIGRATOR]))
    # env.py migrates as app_migrator via DATABASE_MIGRATOR_URL.
    monkeypatch.setenv("DATABASE_MIGRATOR_URL", migrator_url)
    config = build_config()

    # Apply ALL heads (strictly "heads", not "head" — multi-branch layout).
    # Both branches must land: processed_events (shared) and users (core_auth).
    all_tables = (
        "processed_events",
        "users",
        "tenants",
        "memberships",
        "audit_log",
        "currencies",
        "plans",
        "payments",
        "notification_settings",
        "notification_outbox",
        "commerce_products",
        "commerce_carts",
        "commerce_cart_items",
        "commerce_orders",
        "commerce_order_items",
    )
    command.upgrade(config, "heads")
    for table in all_tables:
        assert asyncio.run(_table_exists(migrator_url, table)), table

    # No unapplied heads: a second upgrade is a no-op and everything remains.
    command.upgrade(config, "heads")
    assert asyncio.run(_table_exists(migrator_url, "memberships"))

    # Reversibility: every migration has a working downgrade to each branch base.
    command.downgrade(config, "core_billing@base")
    assert not asyncio.run(_table_exists(migrator_url, "payments"))
    command.downgrade(config, "core_notifications@base")
    assert not asyncio.run(_table_exists(migrator_url, "notification_outbox"))
    command.downgrade(config, "core_audit@base")
    assert not asyncio.run(_table_exists(migrator_url, "audit_log"))
    # commerce_* FK tenants (RESTRICT), so their branches downgrade before tenants.
    command.downgrade(config, "commerce_orders@base")
    assert not asyncio.run(_table_exists(migrator_url, "commerce_orders"))
    command.downgrade(config, "commerce_cart@base")
    assert not asyncio.run(_table_exists(migrator_url, "commerce_carts"))
    command.downgrade(config, "commerce_products@base")
    assert not asyncio.run(_table_exists(migrator_url, "commerce_products"))
    command.downgrade(config, "core_tenants@base")
    assert not asyncio.run(_table_exists(migrator_url, "memberships"))
    command.downgrade(config, "core_auth@base")
    assert not asyncio.run(_table_exists(migrator_url, "users"))
    command.downgrade(config, "shared@base")
    assert not asyncio.run(_table_exists(migrator_url, "processed_events"))

    # Re-apply after the rollback.
    command.upgrade(config, "heads")
    for table in all_tables:
        assert asyncio.run(_table_exists(migrator_url, table)), table


def test_version_locations_are_discovered() -> None:
    from migrations.cli import PROJECT_ROOT

    locations = discover_version_locations(PROJECT_ROOT)
    assert any(path.as_posix().endswith("shared/migrations") for path in locations)
