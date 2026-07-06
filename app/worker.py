"""arq worker: reliable event delivery (interfaces doc §2.6).

Semantics for ``reliable=True`` handlers: at-least-once delivery from arq,
made effectively-once per handler by the ``processed_events`` insert executed
in the SAME transaction as the handler's unit of work. Retries: up to 5 tries
with exponential backoff; after that the failure is logged as a dead letter
(and reaches Sentry via the logging integration).
"""

from typing import Any, ClassVar, cast

import structlog
from arq.connections import RedisSettings
from arq.worker import Retry
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult

import core.subscribers  # noqa: F401  (register core subscribers in the worker process)
from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.logging_setup import configure_logging
from app.observability import init_sentry
from shared import TEMPLATE_VERSION
from shared.context import TenantContext
from shared.events import EventEnvelope, bus
from shared.handler_runtime import HandlerRuntime, reset_handler_runtime, set_handler_runtime
from shared.processed_events import ProcessedEvent
from shared.service import SqlAlchemyUnitOfWork

logger = structlog.stdlib.get_logger(__name__)

MAX_TRIES = 5
RETRY_BASE_DELAY_SECONDS = 15


async def dispatch_event(ctx: dict[str, Any], handler_id: str, wire: dict[str, Any]) -> None:
    envelope = EventEnvelope.from_wire(wire)
    # LookupError is not retried: a handler unknown to this process is a
    # deployment problem, retrying cannot fix it.
    subscription = ctx["bus"].resolve(handler_id)
    job_try = int(ctx.get("job_try") or 1)

    # Reconstruct the tenant context from the envelope so the handler's writes
    # pass RLS in the right tenant (§2.3). Core sinks that write system rows
    # (audit) run under app_maintenance; everything else as app_user.
    handler_ctx = TenantContext(
        tenant_id=envelope.tenant_id,
        actor=envelope.actor,
        request_id=None,
        locale="ru",
    )
    session_factory = (
        ctx["maintenance_sessions"] if subscription.maintenance else ctx["session_factory"]
    )

    try:
        uow = SqlAlchemyUnitOfWork(session_factory, context=handler_ctx)
        async with uow:
            dedup_insert = (
                pg_insert(ProcessedEvent)
                .values(handler=handler_id, event_id=envelope.event_id)
                .on_conflict_do_nothing()
            )
            result = cast("CursorResult[Any]", await uow.session.execute(dedup_insert))
            if result.rowcount == 0:
                logger.info(
                    "duplicate event delivery skipped",
                    event_name=envelope.name,
                    event_id=str(envelope.event_id),
                    handler=handler_id,
                )
                return
            # Expose the dispatcher's unit of work so the handler writes in this
            # same transaction (effectively-once) and can emit further events.
            token = set_handler_runtime(HandlerRuntime(uow=uow, ctx=handler_ctx, bus=ctx["bus"]))
            try:
                await subscription.handler(envelope)
            finally:
                reset_handler_runtime(token)
    except Exception:
        if job_try >= MAX_TRIES:
            logger.exception(
                "event handler dead-lettered",
                event_name=envelope.name,
                event_id=str(envelope.event_id),
                handler=handler_id,
                tries=job_try,
            )
            raise
        defer_seconds = RETRY_BASE_DELAY_SECONDS * 2 ** (job_try - 1)
        logger.warning(
            "event handler failed, will retry",
            event_name=envelope.name,
            event_id=str(envelope.event_id),
            handler=handler_id,
            job_try=job_try,
            defer_seconds=defer_seconds,
            exc_info=True,
        )
        raise Retry(defer=defer_seconds) from None


async def on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_sentry(settings)
    user_engine = create_engine(settings.database_url)
    maintenance_engine = create_engine(settings.database_maintenance_url)
    ctx["user_engine"] = user_engine
    ctx["maintenance_engine"] = maintenance_engine
    ctx["session_factory"] = create_session_factory(user_engine)
    ctx["maintenance_sessions"] = create_session_factory(maintenance_engine)
    ctx["bus"] = bus

    async def enqueue_reliable(handler_id: str, wire: dict[str, Any]) -> None:
        # Handlers may emit events themselves; they are enqueued via the
        # worker's own arq connection.
        await ctx["redis"].enqueue_job("dispatch_event", handler_id, wire)

    bus.bind_enqueue(enqueue_reliable)
    logger.info("worker_started", template_version=TEMPLATE_VERSION)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await ctx["user_engine"].dispose()
    await ctx["maintenance_engine"].dispose()


class WorkerSettings:
    """arq entrypoint: ``arq app.worker.WorkerSettings``."""

    functions: ClassVar[list[Any]] = [dispatch_event]
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)
    max_tries = MAX_TRIES
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
