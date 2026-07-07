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

import core.subscribers  # noqa: F401  (register core event subscribers on the bus)
from app.admin_screens import mount_admin_screens
from app.config import Settings, get_settings
from app.db import Database
from app.features import install_modules
from app.logging_setup import configure_logging
from app.middleware import MetricsMiddleware, RequestIDMiddleware, SecurityHeadersMiddleware
from app.observability import init_sentry
from app.redis_client import create_redis
from app.routes import router as infra_router
from app.startup_checks import validate_admin_routes, validate_route_permissions
from core.admin.permissions import register_admin_rbac
from core.admin.registry import admin_registry
from core.admin.router import router as admin_router
from core.admin.screens import register_admin_screens
from core.audit.permissions import register_audit_rbac
from core.auth.access_service import register_permissions
from core.auth.router import router as auth_router
from core.billing.adapters import build_payment_providers
from core.billing.api import router as billing_api_router
from core.billing.permissions import register_billing_rbac
from core.billing.router import router as billing_webhook_router
from core.files.adapters import build_storage
from core.files.permissions import register_files_rbac
from core.files.router import router as files_router
from core.tenants.permissions import TENANTS_PERMISSIONS
from core.tenants.router import router as tenants_router
from core.tenants.sync import sync_system_roles
from shared import TEMPLATE_VERSION
from shared.encryption import SecretCipher
from shared.error_catalog import ERROR_CATALOG
from shared.errors import DomainError
from shared.events import bus
from shared.i18n import negotiate_locale, parse_accept_language
from shared.money import currency_registry

logger = structlog.stdlib.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    # Declare the permission catalog before the route validator runs. Each module
    # registers its codes + grants; admin screens register on the registry.
    register_permissions("tenants", TENANTS_PERMISSIONS)
    register_billing_rbac()  # billing permissions + their grants to system roles
    register_files_rbac()  # files permissions + their grants to system roles
    register_audit_rbac()  # audit.record:read (owner/admin) — audit admin screen
    register_admin_rbac()  # admin.screen:read (owner/admin) — the admin menu
    # The screen registry is rebuilt per app instance (features register per-app).
    admin_registry.reset()
    register_admin_screens()  # core screens; feature screens are added by the loader

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Fail fast, before any external connection is made.
        validate_route_permissions(app)
        validate_admin_routes(app)  # admin routes: require_permission only (§5.4)

        sentry_enabled = init_sentry(app_settings)
        database = Database(app_settings)
        redis = create_redis(app_settings)
        arq_pool = await create_pool(RedisSettings.from_dsn(app_settings.redis_url))

        async def enqueue_reliable(handler_id: str, wire: dict[str, Any]) -> None:
            await arq_pool.enqueue_job("dispatch_event", handler_id, wire)

        bus.bind_enqueue(enqueue_reliable)

        app.state.settings = app_settings
        app.state.db = database
        app.state.engine = database.user_engine  # readiness probe target
        app.state.session_factory = database.user_sessions
        app.state.redis = redis
        app.state.arq_pool = arq_pool
        app.state.bus = bus
        app.state.cipher = SecretCipher(app_settings.secret_encryption_key_list)
        # Enabled payment providers (Payme/Click); the webhook routes read this.
        # A provider enabled without credentials fails loudly here, at startup.
        app.state.payment_providers = build_payment_providers(app_settings)
        # Object-storage backend for core/files (filesystem/s3). An "s3" backend
        # without credentials fails loudly here, at startup.
        app.state.file_storage = build_storage(app_settings)

        # Idempotently reconcile system roles + grants (as app_maintenance) and
        # load the currency exponents into the process-global registry (§2.5).
        async with database.maintenance_sessions() as session:
            await sync_system_roles(session)
            await currency_registry.load(session)

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
            await database.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version=TEMPLATE_VERSION,
        lifespan=lifespan,
    )
    application.include_router(infra_router)
    application.include_router(auth_router)
    application.include_router(tenants_router)
    application.include_router(billing_api_router)
    application.include_router(billing_webhook_router)
    application.include_router(files_router)
    application.include_router(admin_router)  # /api/admin/screens (the menu)

    # Business-module features (ENABLED_MODULES): discover, validate requires,
    # install RBAC + admin screens + mount routers. A feature with an unmet
    # requires fails here. Runs before mount_admin_screens so feature admin
    # screens (registered in install()) are mounted too.
    install_modules(application, app_settings)

    mount_admin_screens(application)  # /api/admin/{slug} for every registered screen

    @application.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        # Single mapping of the DomainError hierarchy onto HTTP (OV-07).
        # message_key stays machine-readable; message is rendered in the request
        # locale (Accept-Language -> 'ru') via the i18n error catalog (Phase 3).
        locale = negotiate_locale(parse_accept_language(request.headers.get("accept-language")))
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "code": exc.code,
                    "message_key": exc.message_key,
                    "message": ERROR_CATALOG.get(exc.message_key, locale),
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
