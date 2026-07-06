from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

import tests.models  # noqa: F401  (register the test model on the shared metadata)
from app.config import Settings
from app.main import create_app
from shared.context import Actor, TenantContext
from shared.db import Base


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture
async def pg_engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def session_factory(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg_engine, expire_on_commit=False)


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(), actor=Actor(kind="user", id="user-1"), request_id="req-1"
    )


@pytest.fixture
def other_tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(), actor=Actor(kind="user", id="user-2"), request_id="req-2"
    )


@pytest.fixture
def test_settings(postgres_url: str, redis_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        log_level="WARNING",
        database_url=postgres_url,
        redis_url=redis_url,
    )


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    application = create_app(test_settings)
    with TestClient(application) as test_client:
        yield test_client
