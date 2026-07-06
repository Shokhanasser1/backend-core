"""Multi-branch Alembic: upgrade heads applies every branch, downgrade is
reversible, "no unapplied heads after upgrade" (update strategy §3.5)."""

import asyncio
import re

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from migrations.cli import build_config
from migrations.discovery import discover_version_locations

pytestmark = pytest.mark.integration

MIGRATIONS_DB = "migrations_check"


async def _prepare_database(admin_url: str) -> str:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text(f"DROP DATABASE IF EXISTS {MIGRATIONS_DB}"))
        await connection.execute(text(f"CREATE DATABASE {MIGRATIONS_DB}"))
    await engine.dispose()
    return re.sub(r"/[^/?]+$", f"/{MIGRATIONS_DB}", admin_url)


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
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations_url = asyncio.run(_prepare_database(postgres_url))
    monkeypatch.setenv("DATABASE_URL", migrations_url)
    config = build_config()

    # Apply ALL heads (strictly "heads", not "head" — multi-branch layout).
    command.upgrade(config, "heads")
    assert asyncio.run(_table_exists(migrations_url, "processed_events"))

    # "No unapplied heads remain after the upgrade."
    script_heads = set(ScriptDirectory.from_config(config).get_heads())
    db_heads = set(
        asyncio.run(_fetch_scalars(migrations_url, "SELECT version_num FROM alembic_version"))
    )
    assert db_heads == script_heads

    # Reversibility: every migration has a working downgrade.
    command.downgrade(config, "shared@base")
    assert not asyncio.run(_table_exists(migrations_url, "processed_events"))

    # Re-apply after the rollback.
    command.upgrade(config, "heads")
    assert asyncio.run(_table_exists(migrations_url, "processed_events"))


def test_version_locations_are_discovered() -> None:
    from migrations.cli import PROJECT_ROOT

    locations = discover_version_locations(PROJECT_ROOT)
    assert any(path.as_posix().endswith("shared/migrations") for path in locations)
