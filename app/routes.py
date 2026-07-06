"""Infrastructure endpoints: /health (liveness), /ready (readiness), /metrics.

These paths are whitelisted in the startup permission validator — every other
route must declare exactly one permission marker (interfaces doc §5.3).
"""

import asyncio

import structlog
from arq.constants import default_queue_name
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.observability import ARQ_QUEUE_DEPTH
from shared import TEMPLATE_VERSION

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up. No dependency checks here by design."""
    return {"status": "ok", "version": TEMPLATE_VERSION}


async def check_database(engine: AsyncEngine, timeout_seconds: float) -> str:
    try:
        async with asyncio.timeout(timeout_seconds):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness: database check failed", error=repr(exc))
        return "error"
    return "ok"


async def check_redis(redis: Redis, timeout_seconds: float) -> str:
    try:
        async with asyncio.timeout(timeout_seconds):
            await redis.ping()
    except Exception as exc:
        logger.warning("readiness: redis check failed", error=repr(exc))
        return "error"
    return "ok"


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness: verifies PostgreSQL and Redis; 503 degrades traffic routing."""
    settings = request.app.state.settings
    timeout_seconds: float = settings.ready_check_timeout_seconds
    database_status = await check_database(request.app.state.engine, timeout_seconds)
    redis_status = await check_redis(request.app.state.redis, timeout_seconds)
    payload = {"database": database_status, "redis": redis_status}
    all_ok = all(status == "ok" for status in payload.values())
    return JSONResponse(content=payload, status_code=200 if all_ok else 503)


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus exposition: HTTP latency/status counters + arq queue depth."""
    try:
        depth = await request.app.state.redis.zcard(default_queue_name)
        ARQ_QUEUE_DEPTH.labels(queue=default_queue_name).set(float(depth))
    except Exception as exc:
        logger.warning("failed to read arq queue depth", error=repr(exc))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
