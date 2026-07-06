"""Integration tests for Redis-backed rate limiting/lockout (threat model V2)."""

from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from core.auth.rate_limit import (
    EphemeralTokenStore,
    LoginThrottle,
    RateLimiter,
    TotpReplayGuard,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_rate_limiter_blocks_after_limit(redis: Redis) -> None:
    limiter = RateLimiter(redis)
    allowed = [await limiter.hit("login", "1.2.3.4", limit=3, window_seconds=60) for _ in range(4)]
    assert allowed == [True, True, True, False]


async def test_login_throttle_locks_and_clears(redis: Redis) -> None:
    throttle = LoginThrottle(redis, max_failures=3, lockout_seconds=60)
    account = "user-1"
    assert not await throttle.is_locked(account)
    for _ in range(3):
        await throttle.record_failure(account)
    assert await throttle.is_locked(account)
    await throttle.clear(account)
    assert not await throttle.is_locked(account)


async def test_totp_replay_guard(redis: Redis) -> None:
    guard = TotpReplayGuard(redis, window_seconds=90)
    assert await guard.check_and_mark("user-1", "123456") is True
    assert await guard.check_and_mark("user-1", "123456") is False
    assert await guard.check_and_mark("user-1", "654321") is True


async def test_ephemeral_token_store_single_use(redis: Redis) -> None:
    store = EphemeralTokenStore(redis, "auth:pwreset")
    await store.put("hash-1", "user-123", ttl_seconds=60)
    assert await store.peek("hash-1") == "user-123"
    assert await store.consume("hash-1") == "user-123"
    # Consumed → gone.
    assert await store.consume("hash-1") is None
