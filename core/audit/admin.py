"""Audit admin screen (interfaces §3.6): the tenant's activity log.

Registers itself on the admin registry at import time. Mounted by the app under
``/api/admin/audit``; gated by ``audit.record:read`` (owner/admin). This is the
first concrete admin screen and doubles as the reference for how a module wires
one up: declare a router whose every endpoint carries ``require_permission``,
wrap it in an ``AdminScreen``, register it.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from core.admin.registry import AdminScreen, admin_registry
from core.audit import permissions as perms
from core.audit.schemas import AuditQuery, AuditRecordDTO
from core.auth.deps import ServiceBundle, authed_bundle, require_permission
from shared.pagination import MAX_PAGE_LIMIT, Page, PageResult

router = APIRouter()


@router.get("", dependencies=[Depends(require_permission(perms.RECORD_READ))])
async def search_audit(
    bundle: ServiceBundle = Depends(authed_bundle),
    action_prefix: str | None = Query(default=None, max_length=100),
    actor_user_id: UUID | None = Query(default=None),
    object_type: str | None = Query(default=None, max_length=100),
    object_id: str | None = Query(default=None, max_length=200),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> PageResult[AuditRecordDTO]:
    query = AuditQuery(
        action_prefix=action_prefix,
        actor_user_id=actor_user_id,
        object_type=object_type,
        object_id=object_id,
        date_from=date_from,
        date_to=date_to,
    )
    return await bundle.audit.search(query, Page(limit=limit, offset=offset))


AUDIT_SCREEN = AdminScreen(
    slug="audit",
    title_key="admin.screen.audit",
    module="audit",
    router=router,
    permission=perms.RECORD_READ,
)
admin_registry.register(AUDIT_SCREEN)
