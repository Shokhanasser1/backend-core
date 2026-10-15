"""Transaction-scoped advisory locks (PostgreSQL).

Serializes a check-then-write across concurrent requests when there is no row to
lock yet (a count against a limit, "is this the last step?"). The lock is keyed by
an arbitrary string, held until the surrounding transaction commits or rolls back,
and needs no table or grant.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def advisory_xact_lock(session: AsyncSession, key: str) -> None:
    """Block until this transaction holds the lock for ``key`` (released at its end).
    Callers namespace keys (``"<feature>:<tenant_id>:<what>"``)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )
