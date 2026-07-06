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

from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.logging_setup import configure_logging
from app.observability import init_sentry
from shared import TEMPLATE_VERSION
from shared.events import EventEnvelope, bus
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

    try:
        uow = SqlAlchemyUnitOfWork(ctx["session_factory"])
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
            await subscription.handler(envelope)
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
    engine = create_engine(settings)
    ctx["engine"] = engine
    ctx["session_factory"] = create_session_factory(engine)
    ctx["bus"] = bus

    async def enqueue_reliable(handler_id: str, wire: dict[str, Any]) -> None:
        # Handlers may emit events themselves; they are enqueued via the
        # worker's own arq connection.
        await ctx["redis"].enqueue_job("dispatch_event", handler_id, wire)

    bus.bind_enqueue(enqueue_reliable)
    logger.info("worker_started", template_version=TEMPLATE_VERSION)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    """arq entrypoint: ``arq app.worker.WorkerSettings``."""

    functions: ClassVar[list[Any]] = [dispatch_event]
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)
    max_tries = MAX_TRIES
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
