"""Service base and transaction boundary (interfaces doc §2.3).

Events accumulate in the service and are published ONLY after a successful
commit (UnitOfWork post-commit hook). Rollback discards them — no ghost
events. Known v1 limitation (OV-38): a process crash between commit and arq
enqueue loses the event; critical intra-module reactions therefore never go
through the bus.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.context import TenantContext
from shared.events import EventBus, EventEnvelope, validate_event_name
from shared.ids import new_uuid7

logger = logging.getLogger(__name__)

CommitCallback = Callable[[], Awaitable[None]]


class UnitOfWork(Protocol):
    session: AsyncSession

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def on_commit(self, callback: CommitCallback) -> None: ...


class SqlAlchemyUnitOfWork:
    """Commit on clean exit, rollback on exception; post-commit callbacks run
    after a successful commit only, their errors are logged, not raised
    (the transaction is already durable)."""

    session: AsyncSession

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._callbacks: list[CommitCallback] = []

    async def __aenter__(self) -> Self:
        self.session = self._session_factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc is not None:
                await self.session.rollback()
                return
            await self.session.commit()
        finally:
            await self.session.close()
        for callback in self._callbacks:
            try:
                await callback()
            except Exception:
                logger.exception("post-commit callback failed")
        self._callbacks.clear()

    def on_commit(self, callback: CommitCallback) -> None:
        self._callbacks.append(callback)


class Service:
    """Base for all services: holds UnitOfWork, bus and tenant context."""

    def __init__(self, uow: UnitOfWork, bus: EventBus, ctx: TenantContext) -> None:
        self._uow = uow
        self._bus = bus
        self._ctx = ctx
        self._pending_events: list[EventEnvelope] = []

    @property
    def ctx(self) -> TenantContext:
        return self._ctx

    def emit(self, name: str, payload: Mapping[str, Any], *, version: int = 1) -> UUID:
        """Queue an event for the current transaction; returns event_id so the
        caller can link a direct audit record to it (interfaces doc §3.5).
        Publication happens post-commit."""
        validate_event_name(name)
        envelope = EventEnvelope(
            event_id=new_uuid7(),
            name=name,
            version=version,
            occurred_at=datetime.now(UTC),
            tenant_id=self._ctx.tenant_id,
            actor=self._ctx.actor,
            payload=dict(payload),
        )
        if not self._pending_events:
            self._uow.on_commit(self._publish_pending)
        self._pending_events.append(envelope)
        return envelope.event_id

    async def _publish_pending(self) -> None:
        pending, self._pending_events = self._pending_events, []
        for envelope in pending:
            await self._bus.publish(envelope)
