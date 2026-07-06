"""Redis client factory (cache, rate limiting, arq queue depth checks)."""

from redis.asyncio import Redis

from app.config import Settings


def create_redis(settings: Settings) -> Redis:
    client: Redis = Redis.from_url(settings.redis_url)
    return client
