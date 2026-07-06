"""Resilience primitives for outbound calls to external services.

Timeout + bounded retry with exponential backoff + a per-target circuit breaker.
Consumed by adapters that make network calls (notification channels — Telegram,
Eskiz, SMTP — in Phase 3, task 16; and any payment adapter that talks
server-to-server). Pure asyncio, no third-party dependency.

A transient failure (timeout, connection reset, an ``ExternalServiceError``
raised by the adapter) is retried; once the attempts are exhausted the last
error is re-raised. A circuit breaker fails fast with ``CircuitOpenError`` while
a target keeps failing, so a dead provider is not hammered every request.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from shared.errors import CircuitOpenError, ExternalServiceError

logger = logging.getLogger(__name__)

# Failures worth retrying: our own external-service errors plus the low-level
# network/timeout errors an adapter may surface.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    ExternalServiceError,
    TimeoutError,  # asyncio.timeout raises builtin TimeoutError (3.11+)
    ConnectionError,
    OSError,
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff. ``attempts`` counts the total tries (>= 1);
    ``attempts == 1`` disables retrying."""

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.2  # +/- fraction of the computed delay

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("RetryPolicy.attempts must be >= 1")

    def delay_for(self, failed_attempt: int) -> float:
        """Backoff before the retry that follows a failed attempt (1-based)."""
        raw = self.base_delay * (self.multiplier ** (failed_attempt - 1))
        raw = min(raw, self.max_delay)
        if self.jitter:
            # Jitter is anti-thundering-herd only, not security-sensitive.
            raw *= 1 + random.uniform(-self.jitter, self.jitter)  # noqa: S311
        return max(0.0, raw)


class CircuitBreaker:
    """Per-target breaker: closed -> open after ``failure_threshold`` consecutive
    failures, then half-open after ``recovery_time`` to probe recovery. A single
    breaker instance guards one target (e.g. one notification channel)."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_time: float = 30.0,
        name: str = "external",
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_time = recovery_time
        self._clock = clock
        self._failures = 0
        self._opened_at = 0.0
        self._state = "closed"  # "closed" | "open" | "half_open"

    @property
    def state(self) -> str:
        return self._state

    def allow(self) -> bool:
        """Whether a call may proceed now (transitions open -> half_open on time)."""
        if self._state == "open" and self._clock() - self._opened_at >= self._recovery_time:
            self._state = "half_open"
        return self._state != "open"

    def record_success(self) -> None:
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        # A failed probe in half-open, or crossing the threshold in closed, opens it.
        if self._state == "half_open" or self._failures >= self._failure_threshold:
            self._state = "open"
            self._opened_at = self._clock()


async def call_resilient[T](
    operation: Callable[[], Awaitable[T]],
    *,
    timeout: float,  # noqa: ASYNC109 - this IS the asyncio.timeout budget, applied below
    retry: RetryPolicy | None = None,
    breaker: CircuitBreaker | None = None,
    transient: tuple[type[Exception], ...] = TRANSIENT_ERRORS,
    error_cls: type[ExternalServiceError] = ExternalServiceError,
) -> T:
    """Run ``operation`` with a per-attempt timeout, retry/backoff and an optional
    circuit breaker. Re-raises the original error if it is already an
    ``ExternalServiceError``; otherwise wraps it in ``error_cls``. ``CircuitOpenError``
    is raised immediately when the breaker is open (it is never retried here)."""
    policy = retry or RetryPolicy()
    last_exc: BaseException | None = None

    for attempt in range(1, policy.attempts + 1):
        if breaker is not None and not breaker.allow():
            raise CircuitOpenError(f"circuit open for {breaker.name}")
        try:
            async with asyncio.timeout(timeout):
                result = await operation()
        except asyncio.CancelledError:
            raise
        except transient as exc:
            last_exc = exc
            if breaker is not None:
                breaker.record_failure()
            if attempt >= policy.attempts:
                break
            logger.warning(
                "resilient call failed (attempt %d/%d), retrying",
                attempt,
                policy.attempts,
                exc_info=exc,
            )
            await asyncio.sleep(policy.delay_for(attempt))
        else:
            if breaker is not None:
                breaker.record_success()
            return result

    assert last_exc is not None  # noqa: S101 - loop only breaks with a stored error
    if isinstance(last_exc, ExternalServiceError):
        raise last_exc
    raise error_cls(str(last_exc)) from last_exc
