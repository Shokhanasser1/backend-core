"""Ambient runtime for a reliable event handler (interfaces doc §2.3).

A reliable handler runs inside the dispatcher's UnitOfWork + TenantContext
(reconstructed from the envelope). It needs access to that unit of work to
write in the same transaction as the dedup insert (effectively-once) and to
publish further events via ``Service.emit``. The dispatcher publishes this
runtime through a context variable; the handler reads it.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass

from shared.context import TenantContext
from shared.events import EventBus
from shared.service import UnitOfWork


@dataclass(frozen=True, slots=True)
class HandlerRuntime:
    uow: UnitOfWork
    ctx: TenantContext
    bus: EventBus


_current: ContextVar[HandlerRuntime | None] = ContextVar("handler_runtime", default=None)


def set_handler_runtime(runtime: HandlerRuntime) -> Token[HandlerRuntime | None]:
    return _current.set(runtime)


def reset_handler_runtime(token: Token[HandlerRuntime | None]) -> None:
    _current.reset(token)


def current_handler_runtime() -> HandlerRuntime:
    runtime = _current.get()
    if runtime is None:
        raise RuntimeError(
            "no handler runtime in context; a reliable handler must run inside the event dispatcher"
        )
    return runtime
