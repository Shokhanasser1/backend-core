"""Idempotent startup sync of system roles + their grants (schema §2.2).

System roles (owner/admin/member) are DB rows with tenant_id NULL. Their grants
are declared in code (permissions.py) and reconciled here at startup so the
admin-API reads any role's permissions the same way. Runs as app_maintenance
(writes tenant_id NULL rows). Fails startup if a granted code is not in the
permission catalog.
"""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.permissions import permission_registry
from core.tenants.models import Role, RolePermission
from core.tenants.permissions import (
    SYSTEM_ROLE_TITLES,
    system_role_grants,
)
from shared.ids import new_uuid7


async def sync_system_roles(session: AsyncSession) -> None:
    for code, grants in system_role_grants.resolved().items():
        for permission in grants:
            permission_registry.require_registered(permission)

        # Upsert the role row (tenant_id NULL, is_system).
        await session.execute(
            pg_insert(Role)
            .values(
                id=new_uuid7(),
                tenant_id=None,
                code=code,
                name=SYSTEM_ROLE_TITLES[code],
                is_system=True,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "code"],
            )
        )
        role_id = (
            await session.execute(
                select(Role.id).where(Role.tenant_id.is_(None), Role.code == code)
            )
        ).scalar_one()

        # Reconcile grants: add missing, drop stale.
        current = set(
            (
                await session.execute(
                    select(RolePermission.permission_code).where(RolePermission.role_id == role_id)
                )
            )
            .scalars()
            .all()
        )
        for permission in grants - current:
            await session.execute(
                pg_insert(RolePermission)
                .values(role_id=role_id, permission_code=permission)
                .on_conflict_do_nothing()
            )
        stale = current - grants
        if stale:
            await session.execute(
                delete(RolePermission).where(
                    RolePermission.role_id == role_id,
                    RolePermission.permission_code.in_(stale),
                )
            )
    await session.commit()
