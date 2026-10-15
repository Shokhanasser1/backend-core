"""Public read-only tenant lookups for sibling modules (interfaces support).

Billing uses this to address a payment receipt to the tenant owner, crm.activities
to validate an assignee — without reading the tenants/memberships tables
themselves or seeing their ORM across the boundary (ADR-0005) — mirrors core/auth/directory.py.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenants.models import Membership, Tenant


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

    async def is_active_member(self, tenant_id: UUID, user_id: UUID) -> bool:
        """Whether ``user_id`` is an active member of the ACTIVE tenant ``tenant_id``
        (e.g. a valid assignee for a crm activity). A suspended tenant has none."""
        row = (
            await self._session.execute(
                select(Membership.id)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == user_id,
                    Membership.status == "active",
                    Tenant.status == "active",
                )
            )
        ).first()
        return row is not None
