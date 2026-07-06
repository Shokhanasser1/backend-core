"""core/tenants HTTP endpoints (/api/tenants). Every route carries one marker."""

from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, Depends, status

from core.auth.deps import (
    ServiceBundle,
    authed_bundle,
    authenticated_endpoint,
    maintenance_bundle,
    require_permission,
)
from core.tenants import permissions as perms
from core.tenants.schemas import (
    AcceptInvitationIn,
    ChangeMemberRoleIn,
    CreateTenantIn,
    InviteMemberIn,
    MembershipDTO,
    TenantDTO,
    UpdateTenantIn,
)
from shared.pagination import Page, PageResult

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


# --- user-scope (authenticated, no tenant context) ---


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(authenticated_endpoint("user-scoped: create your own organization"))],
)
async def create_tenant(
    body: CreateTenantIn, bundle: ServiceBundle = Depends(maintenance_bundle)
) -> TenantDTO:
    return await bundle.tenants.create_tenant(body.name, body.slug)


@router.get(
    "",
    dependencies=[Depends(authenticated_endpoint("user-scoped: my organizations"))],
)
async def list_my_tenants(bundle: ServiceBundle = Depends(authed_bundle)) -> Sequence[TenantDTO]:
    return await bundle.tenants.list_user_tenants()


@router.post(
    "/invitations/accept",
    dependencies=[Depends(authenticated_endpoint("user-scoped: accept an invitation"))],
)
async def accept_invitation(
    body: AcceptInvitationIn, bundle: ServiceBundle = Depends(maintenance_bundle)
) -> MembershipDTO:
    return await bundle.tenants.accept_invitation(body.invitation_token)


# --- tenant-context (require_permission) ---


@router.get(
    "/current",
    dependencies=[Depends(require_permission(perms.TENANT_READ))],
)
async def get_current_tenant(bundle: ServiceBundle = Depends(authed_bundle)) -> TenantDTO:
    return await bundle.tenants.get_tenant()


@router.patch(
    "/current",
    dependencies=[Depends(require_permission(perms.TENANT_UPDATE))],
)
async def update_current_tenant(
    body: UpdateTenantIn, bundle: ServiceBundle = Depends(authed_bundle)
) -> TenantDTO:
    return await bundle.tenants.update_tenant(name=body.name, default_locale=body.default_locale)


@router.get(
    "/members",
    dependencies=[Depends(require_permission(perms.MEMBER_READ))],
)
async def list_members(
    limit: int = 50, offset: int = 0, bundle: ServiceBundle = Depends(authed_bundle)
) -> PageResult[MembershipDTO]:
    return await bundle.tenants.list_members(Page(limit=limit, offset=offset))


@router.post(
    "/invitations",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perms.MEMBER_INVITE))],
)
async def invite_member(
    body: InviteMemberIn, bundle: ServiceBundle = Depends(authed_bundle)
) -> dict[str, object]:
    dto, raw_token = await bundle.tenants.invite_member(body.email, body.role)
    # v1: the token is returned here until Phase 3 delivers it via notifications.
    return {"invitation": dto.model_dump(mode="json"), "invitation_token": raw_token}


@router.delete(
    "/invitations/{invitation_id}",
    dependencies=[Depends(require_permission(perms.MEMBER_INVITE))],
)
async def revoke_invitation(
    invitation_id: UUID, bundle: ServiceBundle = Depends(authed_bundle)
) -> dict[str, str]:
    await bundle.tenants.revoke_invitation(invitation_id)
    return {"status": "ok"}


@router.patch(
    "/members/{user_id}/role",
    dependencies=[Depends(require_permission(perms.MEMBER_UPDATE_ROLE))],
)
async def change_member_role(
    user_id: UUID, body: ChangeMemberRoleIn, bundle: ServiceBundle = Depends(authed_bundle)
) -> MembershipDTO:
    return await bundle.tenants.change_member_role(user_id, body.role)


@router.delete(
    "/members/{user_id}",
    dependencies=[Depends(require_permission(perms.MEMBER_REMOVE))],
)
async def remove_member(
    user_id: UUID, bundle: ServiceBundle = Depends(authed_bundle)
) -> dict[str, str]:
    await bundle.tenants.remove_member(user_id)
    return {"status": "ok"}
