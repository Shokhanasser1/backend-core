"""Redis rate limiting, login lockout, TOTP anti-replay and short-lived token
store (schema §2.1; threat model V2/V9).

All state is ephemeral with a TTL: losing it degrades safely (a reset limiter,
never a broken login). Login failures are counted per-account independently of
IP so a distributed brute force of one account is still throttled (V2).
"""

from redis.asyncio import Redis

RATE_PREFIX = "rl"
FAIL_PREFIX = "auth:fail"
LOCK_PREFIX = "auth:lock"
TOTP_USED_PREFIX = "auth:totp_used"


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, scope: str, key: str, *, limit: int, window_seconds: int) -> bool:
        """Atomic fixed-window counter. Returns True if allowed, False if the
        limit is exceeded within the window."""
        redis_key = f"{RATE_PREFIX}:{scope}:{key}"
        count = int(await self._redis.incr(redis_key))
        if count == 1:
            await self._redis.expire(redis_key, window_seconds)
        return count <= limit


class LoginThrottle:
    """Per-account failure counter with exponential lockout (V2)."""

    def __init__(self, redis: Redis, *, max_failures: int, lockout_seconds: int) -> None:
        self._redis = redis
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds

    async def is_locked(self, account_key: str) -> bool:
        return bool(await self._redis.exists(f"{LOCK_PREFIX}:{account_key}"))

    async def record_failure(self, account_key: str) -> None:
        fail_key = f"{FAIL_PREFIX}:{account_key}"
        failures = int(await self._redis.incr(fail_key))
        if failures == 1:
            await self._redis.expire(fail_key, self._lockout_seconds)
        if failures >= self._max_failures:
            # Exponential backoff capped at lockout_seconds: 2^(extra) minutes.
            over = failures - self._max_failures
            duration = min(self._lockout_seconds, 60 * (2**over))
            await self._redis.set(f"{LOCK_PREFIX}:{account_key}", "1", ex=duration)

    async def clear(self, account_key: str) -> None:
        await self._redis.delete(f"{FAIL_PREFIX}:{account_key}", f"{LOCK_PREFIX}:{account_key}")


class TotpReplayGuard:
    """Rejects a TOTP code already accepted within its validity window."""

    def __init__(self, redis: Redis, *, window_seconds: int = 90) -> None:
        self._redis = redis
        self._window_seconds = window_seconds

    async def check_and_mark(self, user_id: str, code: str) -> bool:
        """Returns True if the code is fresh (and marks it), False if replayed."""
        key = f"{TOTP_USED_PREFIX}:{user_id}:{code}"
        was_set = await self._redis.set(key, "1", ex=self._window_seconds, nx=True)
        return bool(was_set)


class EphemeralTokenStore:
    """Short-lived opaque tokens keyed by their hash → payload (schema §2.1):
    password reset, 2FA challenge, email verification. Stored in Redis with a
    TTL; consumed atomically so a token is single-use."""

    def __init__(self, redis: Redis, namespace: str) -> None:
        self._redis = redis
        self._namespace = namespace

    def _key(self, token_hash: str) -> str:
        return f"{self._namespace}:{token_hash}"

    async def put(self, token_hash: str, value: str, *, ttl_seconds: int) -> None:
        await self._redis.set(self._key(token_hash), value, ex=ttl_seconds)

    async def consume(self, token_hash: str) -> str | None:
        """Return the payload and delete the key atomically (single use)."""
        key = self._key(token_hash)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.delete(key)
            value, _ = await pipe.execute()
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def peek(self, token_hash: str) -> str | None:
        value = await self._redis.get(self._key(token_hash))
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)
