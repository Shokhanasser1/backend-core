"""SystemRepository: tenant-filter-free READ access for core internals.

Allowed importers: core/ only — enforced by an import-linter contract
(pyproject: "SystemRepository — только для core/"), features never see it.
The only legitimate use is identifying an object before tenant context exists
(e.g. finding a payment by a webhook), after which the caller ELEVATES the
context (interfaces doc §2.1) and continues as a regular tenant-scoped write.
System context never writes business data — hence no add/delete here.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import ColumnExpressionArgument

from shared.db import Base


class SystemRepository[SM: Base]:
    model: type[SM]

    def __init__(self, session: AsyncSession) -> None:
        if getattr(type(self), "model", None) is None:
            raise TypeError(f"{type(self).__name__} must define the 'model' class attribute")
        self._session = session

    async def get_one_or_none(self, *where: ColumnElement[bool]) -> SM | None:
        stmt = select(self.model).where(*where).limit(2)
        result = await self._session.execute(stmt)
        found = result.scalars().all()
        if len(found) > 1:
            raise ValueError(f"get_one_or_none matched {len(found)}+ rows of {self.model.__name__}")
        return found[0] if found else None

    async def find(
        self,
        *where: ColumnElement[bool],
        order_by: Sequence[ColumnExpressionArgument[Any]] | None = None,
        limit: int | None = None,
    ) -> Sequence[SM]:
        stmt = select(self.model).where(*where)
        if order_by is not None:
            stmt = stmt.order_by(*order_by)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count(self, *where: ColumnElement[bool]) -> int:
        stmt = select(func.count()).select_from(self.model).where(*where)
        result = await self._session.execute(stmt)
        return result.scalar_one()
