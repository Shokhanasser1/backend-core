"""Test fixtures.

Integration tests run against a real Postgres (testcontainers) with the full
DB-role separation. The schema is built ONCE per session by the real Alembic
migrations (as app_migrator) — so RLS policies, grants and helper functions are
exactly what production gets — and each test starts from a truncated database.
The app and tests query as app_user, so RLS is genuinely exercised (V1).
"""

import asyncio
import os
import re
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.config import Settings
from app.main import create_app
from shared.context import Actor, TenantContext
from shared.db_provisioning import (
    ROLE_MAINTENANCE,
    ROLE_MIGRATOR,
    ROLE_RETENTION,
    ROLE_USER,
    render_role_bootstrap_statements,
)

# A test-only tenant-scoped table for the generic Repository/RLS tests, created
# alongside the migrated schema.
_TEST_GADGETS_DDL = (
    "CREATE TABLE IF NOT EXISTS test_gadgets ("
    "id uuid PRIMARY KEY, tenant_id uuid NOT NULL, name text NOT NULL, "
    "created_at timestamptz NOT NULL DEFAULT now(), "
    "updated_at timestamptz NOT NULL DEFAULT now())"
)
_TEST_GADGETS_POLICIES = (
    "ALTER TABLE test_gadgets ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_isolation ON test_gadgets FOR ALL TO app_user "
    "USING (tenant_id = app_current_tenant_id()) "
    "WITH CHECK (tenant_id = app_current_tenant_id())",
    "CREATE POLICY maintenance_all ON test_gadgets FOR ALL TO app_maintenance "
    "USING (true) WITH CHECK (true)",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON test_gadgets TO app_user, app_maintenance",
)


def _with_credentials(url: str, user: str, password: str) -> str:
    """Swap the userinfo of a SQLAlchemy asyncpg URL, keeping host/port/db."""
    return re.sub(r"://[^@]+@", f"://{user}:{password}@", url, count=1)


@pytest.fixture(scope="session")
def superuser_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def role_urls(superuser_url: str) -> dict[str, str]:
    """Provision the DB roles once and return per-role connection URLs."""

    async def provision() -> None:
        engine = create_async_engine(superuser_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                for statement in render_role_bootstrap_statements():
                    await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(provision())
    return {
        role: _with_credentials(superuser_url, role, role)
        for role in (ROLE_MIGRATOR, ROLE_USER, ROLE_MAINTENANCE, ROLE_RETENTION)
    }


@pytest.fixture(scope="session")
def _schema(role_urls: dict[str, str]) -> None:
    """Build the schema once via the real migrations (as app_migrator), then add
    the test-only table. RLS/grants/functions come straight from the migrations."""
    from alembic import command

    from migrations.cli import build_config

    os.environ["DATABASE_MIGRATOR_URL"] = role_urls[ROLE_MIGRATOR]
    command.upgrade(build_config(), "heads")

    async def add_test_table() -> None:
        engine = create_async_engine(role_urls[ROLE_MIGRATOR])
        async with engine.begin() as connection:
            await connection.execute(text(_TEST_GADGETS_DDL))
            for statement in _TEST_GADGETS_POLICIES:
                await connection.execute(text(statement))
        await engine.dispose()

    asyncio.run(add_test_table())


@pytest.fixture
async def _clean_db(role_urls: dict[str, str], _schema: None, redis_url: str) -> None:
    """Truncate all tables (as owner) and flush Redis before each test for isolation.

    Redis holds rate-limit counters, lockouts, ephemeral tokens and SMS caps; all
    TestClient requests share one IP, so a per-IP limiter (e.g. login: 30/60s) would
    otherwise leak across tests and make the suite order-dependent."""
    from redis.asyncio import Redis

    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as connection:
            names = (
                (
                    await connection.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                            "AND tablename <> 'alembic_version'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            if names:
                joined = ", ".join(names)
                await connection.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()

    redis = Redis.from_url(redis_url)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()


@pytest.fixture
async def pg_engine(role_urls: dict[str, str], _clean_db: None) -> AsyncIterator[AsyncEngine]:
    """Runtime engine (connects as app_user — RLS applies)."""
    engine = create_async_engine(role_urls[ROLE_USER])
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg_engine, expire_on_commit=False)


@pytest.fixture
async def maintenance_engine(
    role_urls: dict[str, str], _clean_db: None
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(role_urls[ROLE_MAINTENANCE])
    yield engine
    await engine.dispose()


@pytest.fixture
def maintenance_session_factory(
    maintenance_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(maintenance_engine, expire_on_commit=False)


@pytest.fixture
async def retention_engine(
    role_urls: dict[str, str], _clean_db: None
) -> AsyncIterator[AsyncEngine]:
    """Engine as app_retention — SELECT + DELETE on audit_log only (schema §3.1)."""
    engine = create_async_engine(role_urls[ROLE_RETENTION])
    yield engine
    await engine.dispose()


@pytest.fixture
def retention_session_factory(
    retention_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(retention_engine, expire_on_commit=False)


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(), actor=Actor(kind="user", id=str(uuid4())), request_id="req-1"
    )


@pytest.fixture
def other_tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(), actor=Actor(kind="user", id=str(uuid4())), request_id="req-2"
    )


@pytest.fixture
def test_settings(
    role_urls: dict[str, str], redis_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        log_level="WARNING",
        database_url=role_urls[ROLE_USER],
        database_migrator_url=role_urls[ROLE_MIGRATOR],
        database_maintenance_url=role_urls[ROLE_MAINTENANCE],
        database_retention_url=role_urls[ROLE_RETENTION],
        redis_url=redis_url,
        # core/files: filesystem backend rooted in a fresh per-test tmp dir, so
        # uploads never leak into the repo and never collide across tests.
        files_filesystem_root=str(tmp_path_factory.mktemp("files")),
    )


@pytest.fixture
def client(test_settings: Settings, _clean_db: None) -> Iterator[TestClient]:
    application = create_app(test_settings)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def commerce_client(
    test_settings: Settings, role_urls: dict[str, str], _clean_db: None
) -> Iterator[TestClient]:
    """A TestClient with ENABLED_MODULES=commerce and a seeded UZS currency.

    Lives in the root conftest (not under modules/) so feature tests get a running
    app WITHOUT importing app.* — that would violate the modules -> app layer
    boundary (import-linter). commerce.products validates prices against the
    currency registry, loaded at lifespan from the currencies table."""

    async def _seed() -> None:
        engine = create_async_engine(role_urls[ROLE_MIGRATOR])
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO currencies (code, exponent, name) VALUES ('UZS', 0, 'Som') "
                        "ON CONFLICT (code) DO NOTHING"
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    application = create_app(test_settings.model_copy(update={"enabled_modules": "commerce"}))
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def saas_client(
    test_settings: Settings, role_urls: dict[str, str], _clean_db: None
) -> Iterator[TestClient]:
    """A TestClient with ENABLED_MODULES=saas, so the loader mounts the saas
    feature routers and registers their RBAC + bus subscribers.

    Lives in the root conftest (not under modules/) so feature tests get a running
    app WITHOUT importing app.* — that would violate the modules -> app layer
    boundary (import-linter). No currency seed is needed (entitlements does not
    touch billing's currency registry)."""
    application = create_app(test_settings.model_copy(update={"enabled_modules": "saas"}))
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def saas_onboarding_client(
    test_settings: Settings, role_urls: dict[str, str], _clean_db: None
) -> Iterator[TestClient]:
    """saas_client with a configured onboarding checklist (SAAS_ONBOARDING_STEPS),
    so the /me + complete-step routes have a step set to report against."""
    settings = test_settings.model_copy(
        update={
            "enabled_modules": "saas",
            "saas_onboarding_steps": "create_shop,add_product,connect_payment",
        }
    )
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def commerce_payments_client(
    test_settings: Settings, role_urls: dict[str, str], _clean_db: None
) -> Iterator[TestClient]:
    """commerce_client + Payme enabled — orders need a payment provider to checkout."""

    async def _seed() -> None:
        engine = create_async_engine(role_urls[ROLE_MIGRATOR])
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO currencies (code, exponent, name) VALUES ('UZS', 0, 'Som') "
                        "ON CONFLICT (code) DO NOTHING"
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    settings = test_settings.model_copy(
        update={
            "enabled_modules": "commerce",
            "enabled_payment_providers": "payme",
            "payme_merchant_id": "merchant-1",
            "payme_merchant_key": "test-merchant-key",
        }
    )
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client
