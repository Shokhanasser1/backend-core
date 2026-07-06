"""Async engines per DB role and the transaction-local tenant context for RLS.

Role separation (schema §3.1): the runtime engine connects as ``app_user`` so
RLS is enforced; a separate engine connects as ``app_maintenance`` for the
exhaustive list of cross-tenant operations (§3.4). Each transaction sets
``app.tenant_id`` / ``app.user_id`` transaction-locally (``SET LOCAL`` via
set_config(..., true)); the value auto-clears on commit/rollback, so a pooled
connection can never leak one tenant's context into another's transaction
(threat model V1).
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class Database:
    """Holds the per-role engines and session factories for the app lifespan."""

    def __init__(self, settings: Settings) -> None:
        self.user_engine = create_engine(settings.database_url)
        self.maintenance_engine = create_engine(settings.database_maintenance_url)
        self.user_sessions = create_session_factory(self.user_engine)
        self.maintenance_sessions = create_session_factory(self.maintenance_engine)

    async def dispose(self) -> None:
        await self.user_engine.dispose()
        await self.maintenance_engine.dispose()
