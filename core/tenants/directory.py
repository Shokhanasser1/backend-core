"""Public read-only tenant lookups for sibling modules (interfaces support).

Billing uses this to address a payment receipt to the tenant owner without
reading the tenants table itself or seeing the Tenant ORM across the boundary
(ADR-0005) — mirrors core/auth/directory.py.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenants.models import Tenant


class TenantDirectory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owner_user_id(self, tenant_id: UUID) -> UUID | None:
        return (
            await self._session.execute(select(Tenant.owner_user_id).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()

    async def get_default_locale(self, tenant_id: UUID) -> str | None:
        return (
            await self._session.execute(select(Tenant.default_locale).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
