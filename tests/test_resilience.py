"""Unit tests for shared/resilience.py (timeout + retry/backoff + circuit breaker).

No I/O: operations are in-memory coroutines and the breaker runs on a fake clock,
so the whole file is fast and needs no Docker."""

import asyncio

import pytest

from shared.errors import CircuitOpenError, ExternalServiceError, PaymentProviderError
from shared.resilience import CircuitBreaker, RetryPolicy, call_resilient

# base_delay=0 keeps retries instant (0 * multiplier == 0, no real sleep).
_FAST = RetryPolicy(attempts=3, base_delay=0.0, jitter=0.0)


class _Counter:
    def __init__(self) -> None:
        self.calls = 0


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_retry_succeeds_after_transient_failures() -> None:
    counter = _Counter()

    async def flaky() -> str:
        counter.calls += 1
        if counter.calls < 3:
            raise PaymentProviderError("temporary")
        return "ok"

    result = await call_resilient(flaky, timeout=1.0, retry=_FAST)
    assert result == "ok"
    assert counter.calls == 3


async def test_exhausted_domain_error_is_reraised_unchanged() -> None:
    counter = _Counter()
    original = PaymentProviderError("still down")

    async def always_fails() -> str:
        counter.calls += 1
        raise original

    with pytest.raises(PaymentProviderError) as excinfo:
        await call_resilient(always_fails, timeout=1.0, retry=_FAST)
    assert excinfo.value is original
    assert counter.calls == 3


async def test_non_transient_error_is_not_retried() -> None:
    counter = _Counter()

    async def bug() -> str:
        counter.calls += 1
        raise ValueError("programming error")

    with pytest.raises(ValueError, match="programming error"):
        await call_resilient(bug, timeout=1.0, retry=_FAST)
    assert counter.calls == 1  # ValueError is not transient -> no retry


async def test_low_level_error_is_wrapped_in_error_cls() -> None:
    async def always_fails() -> str:
        raise ConnectionError("reset")

    with pytest.raises(PaymentProviderError) as excinfo:
        await call_resilient(always_fails, timeout=1.0, retry=_FAST, error_cls=PaymentProviderError)
    assert isinstance(excinfo.value.__cause__, ConnectionError)


async def test_timeout_is_transient_and_retried() -> None:
    counter = _Counter()

    async def slow_then_fast() -> str:
        counter.calls += 1
        if counter.calls == 1:
            await asyncio.sleep(1.0)  # exceeds the timeout on the first try
        return "ok"

    result = await call_resilient(slow_then_fast, timeout=0.05, retry=_FAST)
    assert result == "ok"
    assert counter.calls == 2


def test_breaker_opens_after_threshold_and_recovers() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, recovery_time=10.0, clock=clock)

    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state == "closed"
    breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow() is False  # still within recovery window

    clock.now = 10.0  # recovery window elapsed
    assert breaker.allow() is True
    assert breaker.state == "half_open"

    breaker.record_success()
    assert breaker.state == "closed"


def test_breaker_reopens_on_failed_probe() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, recovery_time=5.0, clock=clock)
    breaker.record_failure()
    assert breaker.state == "open"
    clock.now = 5.0
    assert breaker.allow() is True  # half-open probe
    breaker.record_failure()  # probe fails
    assert breaker.state == "open"


async def test_open_circuit_fails_fast_without_calling_operation() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, recovery_time=100.0, clock=clock)
    breaker.record_failure()  # trip it
    counter = _Counter()

    async def op() -> str:
        counter.calls += 1
        return "unreached"

    with pytest.raises(CircuitOpenError):
        await call_resilient(op, timeout=1.0, retry=_FAST, breaker=breaker)
    assert counter.calls == 0


async def test_success_records_and_returns_value() -> None:
    breaker = CircuitBreaker(failure_threshold=2)

    async def op() -> int:
        return 42

    assert await call_resilient(op, timeout=1.0, breaker=breaker) == 42
    assert breaker.state == "closed"


def test_retry_policy_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        RetryPolicy(attempts=0)


def test_retry_policy_backoff_is_bounded() -> None:
    policy = RetryPolicy(attempts=10, base_delay=1.0, multiplier=2.0, max_delay=8.0, jitter=0.0)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(4) == 8.0
    assert policy.delay_for(9) == 8.0  # capped at max_delay


async def test_generic_transient_wrapping_uses_default_external_error() -> None:
    async def always_fails() -> str:
        raise TimeoutError

    with pytest.raises(ExternalServiceError):
        await call_resilient(always_fails, timeout=1.0, retry=RetryPolicy(attempts=1))
