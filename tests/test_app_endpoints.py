"""Infrastructure endpoints, security headers, request_id, metrics."""

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from app.routes import check_database, check_redis
from shared import TEMPLATE_VERSION

pytestmark = pytest.mark.integration


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": TEMPLATE_VERSION}


def test_ready_all_dependencies_ok(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"database": "ok", "redis": "ok"}


def test_security_headers_present(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["strict-transport-security"] == ("max-age=31536000; includeSubDomains")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_request_id_generated_and_echoed(client: TestClient) -> None:
    generated = client.get("/health").headers["x-request-id"]
    assert generated

    echoed = client.get("/health", headers={"X-Request-ID": "trace-42"})
    assert echoed.headers["x-request-id"] == "trace-42"


def test_metrics_exposition(client: TestClient) -> None:
    client.get("/health")  # populate the counters
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "arq_queue_depth" in body


class TestReadinessDegradation:
    """Dependency checks degrade to 'error' instead of raising."""

    @pytest.mark.integration
    async def test_database_unreachable(self) -> None:
        engine = create_async_engine("postgresql+asyncpg://nobody:nothing@127.0.0.1:9/void")
        try:
            assert await check_database(engine, timeout_seconds=1.5) == "error"
        finally:
            await engine.dispose()

    async def test_redis_unreachable(self) -> None:
        redis: Redis = Redis.from_url("redis://127.0.0.1:9/0")
        try:
            assert await check_redis(redis, timeout_seconds=1.5) == "error"
        finally:
            await redis.aclose()
