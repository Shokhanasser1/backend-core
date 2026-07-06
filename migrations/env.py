"""Alembic environment: async engine, URL from the environment only."""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import core.audit.models
import core.auth.models
import core.billing.models
import core.notifications.models
import core.tenants.models  # noqa: F401  (register tenants models on the metadata)
import shared.processed_events  # noqa: F401  (register service tables on the metadata)
from app.config import Settings
from shared.db import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # Migrations connect as app_migrator (owner), never as the runtime app_user
    # (schema §3.1). A fresh Settings() (not the cached get_settings) so a URL
    # set by tooling/tests right before invocation is always respected.
    return Settings().database_migrator_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
