"""Per-tenant daily SMS cap (OV-25, threat model: SMS-abuse / cost control).

A Redis counter per (tenant, UTC day) with a ~25h TTL. INCR-first is atomic and
safe under concurrent dispatchers; a rejected attempt still counts (conservative
— it never lets more than the cap through). cap <= 0 disables the limit.
Platform sends (tenant_id NULL) share a single 'platform' bucket.
"""

from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

_TTL_SECONDS = 25 * 3600  # a bit over a day so the bucket outlives its UTC day


class SmsDailyCap:
    def __init__(self, redis: Redis, cap_per_tenant: int) -> None:
        self._redis = redis
        self._cap = cap_per_tenant

    async def try_consume(self, tenant_id: UUID | None) -> bool:
        """Reserve one SMS for today; False if the tenant is already at the cap."""
        if self._cap <= 0:
            return True  # unlimited
        bucket = tenant_id if tenant_id is not None else "platform"
        day = datetime.now(UTC).strftime("%Y%m%d")
        key = f"sms:cap:{bucket}:{day}"
        count = int(await self._redis.incr(key))
        if count == 1:
            await self._redis.expire(key, _TTL_SECONDS)
        return count <= self._cap
