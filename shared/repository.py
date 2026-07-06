"""Repositories with automatic tenant scoping (interfaces doc §2.2).

Repository is the FIRST line of tenant isolation: every query is built through
a single point that appends ``WHERE tenant_id = ctx.tenant_id`` and every
INSERT stamps tenant_id from the context. RLS in Postgres is the independent
second line (arrives with the first tenant tables in Phase 2).
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.expression import ColumnExpressionArgument

from shared.context import TenantContext
from shared.db import GlobalBase, TenantScopedBase
from shared.errors import InvariantViolationError, NotFoundError, PermissionDeniedError
from shared.pagination import Page, PageResult


class Repository[M: TenantScopedBase]:
    """All queries are automatically constrained by ``tenant_id = ctx.tenant_id``;
    INSERT stamps tenant_id from the context. Subclasses cannot bypass the
    filter — it is baked into the single query-building point (``_select``)."""

    model: type[M]

    def __init__(self, session: AsyncSession, ctx: TenantContext) -> None:
        if getattr(type(self), "model", None) is None:
            raise TypeError(f"{type(self).__name__} must define the 'model' class attribute")
        if ctx.tenant_id is None:
            raise InvariantViolationError(
                "tenant-scoped repository requires ctx.tenant_id; "
                "system paths must use SystemRepository (core/ only) and elevate context"
            )
        self._session = session
        self._tenant_id: UUID = ctx.tenant_id

    def _select(self) -> Select[tuple[M]]:
        """The single point where queries are built — the tenant filter lives here."""
        return select(self.model).where(self.model.tenant_id == self._tenant_id)

    async def get(self, entity_id: UUID) -> M | None:
        stmt = self._select().where(self.model.id == entity_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_raise(self, entity_id: UUID) -> M:
        entity = await self.get(entity_id)
        if entity is None:
            # A foreign tenant's object is indistinguishable from a missing one
            # (404, not 403 — threat model V1).
            raise NotFoundError(f"{self.model.__name__} {entity_id} not found")
        return entity

    async def find(
        self,
        *where: ColumnElement[bool],
        order_by: Sequence[ColumnExpressionArgument[Any]] | None = None,
        page: Page | None = None,
    ) -> Sequence[M]:
        stmt = self._select().where(*where)
        if order_by is not None:
            stmt = stmt.order_by(*order_by)
        if page is not None:
            stmt = stmt.limit(page.limit).offset(page.offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def find_paged(
        self,
        *where: ColumnElement[bool],
        order_by: Sequence[ColumnExpressionArgument[Any]] | None = None,
        page: Page,
    ) -> PageResult[M]:
        items = await self.find(*where, order_by=order_by, page=page)
        total = await self.count(*where)
        return PageResult(items=items, total=total, limit=page.limit, offset=page.offset)

    async def count(self, *where: ColumnElement[bool]) -> int:
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.tenant_id == self._tenant_id, *where)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def add(self, entity: M) -> M:
        # getattr: the attribute is None until stamped, while its Mapped type is UUID.
        preset_tenant_id = getattr(entity, "tenant_id", None)
        if preset_tenant_id is not None and preset_tenant_id != self._tenant_id:
            raise PermissionDeniedError("entity is stamped with another tenant_id")
        entity.tenant_id = self._tenant_id
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def delete(self, entity: M) -> None:
        if entity.tenant_id != self._tenant_id:
            raise PermissionDeniedError("entity belongs to another tenant")
        await self._session.delete(entity)


class GlobalRepository[G: GlobalBase]:
    """Repository for global tables (users, currencies, plans): no tenant
    filter, and — by the type bound — no way to touch tenant-scoped data."""

    model: type[G]

    def __init__(self, session: AsyncSession) -> None:
        model = getattr(type(self), "model", None)
        if model is None:
            raise TypeError(f"{type(self).__name__} must define the 'model' class attribute")
        if issubclass(model, TenantScopedBase):
            raise TypeError("GlobalRepository must not be used with tenant-scoped models")
        self._session = session

    async def get(self, pk: Any) -> G | None:
        return await self._session.get(self.model, pk)

    async def get_or_raise(self, pk: Any) -> G:
        entity = await self.get(pk)
        if entity is None:
            raise NotFoundError(f"{self.model.__name__} {pk} not found")
        return entity

    async def find(
        self,
        *where: ColumnElement[bool],
        order_by: Sequence[ColumnExpressionArgument[Any]] | None = None,
        page: Page | None = None,
    ) -> Sequence[G]:
        stmt = select(self.model).where(*where)
        if order_by is not None:
            stmt = stmt.order_by(*order_by)
        if page is not None:
            stmt = stmt.limit(page.limit).offset(page.offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count(self, *where: ColumnElement[bool]) -> int:
        stmt = select(func.count()).select_from(self.model).where(*where)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def add(self, entity: G) -> G:
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def delete(self, entity: G) -> None:
        await self._session.delete(entity)
