"""FastAPI composition root: middleware, infra routes, lifespan wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.logging_setup import configure_logging
from app.middleware import MetricsMiddleware, RequestIDMiddleware, SecurityHeadersMiddleware
from app.observability import init_sentry
from app.redis_client import create_redis
from app.routes import router as infra_router
from app.startup_checks import validate_route_permissions
from shared import TEMPLATE_VERSION
from shared.errors import DomainError
from shared.events import bus

logger = structlog.stdlib.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Fail fast, before any external connection is made.
        validate_route_permissions(app)

        sentry_enabled = init_sentry(app_settings)
        engine = create_engine(app_settings)
        session_factory = create_session_factory(engine)
        redis = create_redis(app_settings)
        arq_pool = await create_pool(RedisSettings.from_dsn(app_settings.redis_url))

        async def enqueue_reliable(handler_id: str, wire: dict[str, Any]) -> None:
            await arq_pool.enqueue_job("dispatch_event", handler_id, wire)

        bus.bind_enqueue(enqueue_reliable)

        app.state.settings = app_settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.redis = redis
        app.state.arq_pool = arq_pool

        logger.info(
            "application_started",
            template_version=TEMPLATE_VERSION,
            app_env=app_settings.app_env,
            enabled_modules=list(app_settings.enabled_module_list),
            sentry_enabled=sentry_enabled,
        )
        try:
            yield
        finally:
            await arq_pool.aclose()
            await redis.aclose()
            await engine.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version=TEMPLATE_VERSION,
        lifespan=lifespan,
    )
    application.include_router(infra_router)

    @application.exception_handler(DomainError)
    async def handle_domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        # Single mapping of the DomainError hierarchy onto HTTP (OV-07).
        # message_key is resolved via i18n catalogs starting from Phase 3.
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "code": exc.code,
                    "message_key": exc.message_key,
                    "detail": exc.detail,
                }
            },
        )

    # add_middleware is LIFO: RequestID ends up outermost, then CORS,
    # then metrics, then security headers closest to the router.
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(MetricsMiddleware)
    if app_settings.cors_origin_list:
        # Strict allowlist from config; no origins configured = no CORS at all.
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(app_settings.cors_origin_list),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type"],
        )
    application.add_middleware(RequestIDMiddleware)
    return application


app = create_app()
