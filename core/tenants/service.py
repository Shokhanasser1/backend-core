"""TenantService — organizations, memberships, roles, invitations (§3.2).

Owner of the user-tenant-role link. Tenant-context methods run as app_user (RLS
scopes them); the two cross-tenant operations — create_tenant and
accept_invitation — must be constructed with a maintenance-bound unit of work
(schema §3.4), because at that moment there is no tenant context yet.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from core.tenants.models import Invitation, Membership, Role, RolePermission, Tenant
from core.tenants.permissions import ROLE_OWNER
from core.tenants.repository import InvitationRepository, MembershipRepository
from core.tenants.schemas import InvitationDTO, MembershipDTO, TenantDTO
from shared.context import TenantContext
from shared.errors import ConflictError, InvariantViolationError, NotFoundError
from shared.events import EventBus
from shared.ids import new_uuid7
from shared.pagination import Page, PageResult
from shared.security_tokens import hash_token, new_opaque_token
from shared.service import Service, UnitOfWork

INVITATION_TTL_DAYS = 7


def _tenant_dto(t: Tenant) -> TenantDTO:
    return TenantDTO(
        id=t.id, name=t.name, slug=t.slug, status=t.status, default_locale=t.default_locale
    )


def _membership_dto(m: Membership, role_code: str) -> MembershipDTO:
    return MembershipDTO(
        id=m.id,
        tenant_id=m.tenant_id,
        user_id=m.user_id,
        role_id=m.role_id,
        role_code=role_code,
        status=m.status,
    )


class TenantService(Service):
    def __init__(self, uow: UnitOfWork, bus: EventBus, ctx: TenantContext) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session

    # ------------------------------------------------------------------ reads
    async def get_permission_codes(self, user_id: UUID) -> frozenset[str]:
        """Resolve a user's permission set in the current tenant via their role."""
        stmt = (
            select(RolePermission.permission_code)
            .join(Membership, Membership.role_id == RolePermission.role_id)
            .where(
                Membership.tenant_id == self.ctx.tenant_id,
                Membership.user_id == user_id,
                Membership.status == "active",
            )
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return frozenset(rows)

    async def has_active_membership(self, tenant_id: UUID, user_id: UUID) -> bool:
        """Membership check by explicit tenant (own rows are visible via RLS
        tenant_or_own even without a tenant context) — used for tenant selection."""
        row = (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == user_id,
                    Membership.status == "active",
                )
            )
        ).first()
        return row is not None

    async def get_membership(self, user_id: UUID) -> MembershipDTO | None:
        stmt = (
            select(Membership, Role.code)
            .join(Role, Role.id == Membership.role_id)
            .where(Membership.tenant_id == self.ctx.tenant_id, Membership.user_id == user_id)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        membership, role_code = row
        return _membership_dto(membership, role_code)

    async def get_tenant(self) -> TenantDTO:
        tenant = await self._session.get(Tenant, self.ctx.tenant_id)
        if tenant is None:
            raise NotFoundError("tenant not found")
        return _tenant_dto(tenant)

    async def list_user_tenants(self) -> Sequence[TenantDTO]:
        """Tenants the current user is a member of (for context selection)."""
        actor_id = self._actor_user_id()
        stmt = (
            select(Tenant)
            .join(Membership, Membership.tenant_id == Tenant.id)
            .where(Membership.user_id == actor_id, Tenant.status == "active")
            .order_by(Tenant.created_at)
        )
        tenants = (await self._session.execute(stmt)).scalars().all()
        return [_tenant_dto(t) for t in tenants]

    async def list_members(self, page: Page) -> PageResult[MembershipDTO]:
        stmt = (
            select(Membership, Role.code)
            .join(Role, Role.id == Membership.role_id)
            .where(Membership.tenant_id == self.ctx.tenant_id)
            .order_by(Membership.created_at)
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(stmt)).all()
        repo = MembershipRepository(self._session, self.ctx)
        total = await repo.count()
        items = [_membership_dto(m, code) for m, code in rows]
        return PageResult(items=items, total=total, limit=page.limit, offset=page.offset)

    # ------------------------------------------------------------ user-context
    async def create_tenant(self, name: str, slug: str) -> TenantDTO:
        """Cross-tenant (maintenance UoW): create the org + owner membership."""
        actor_id = self._actor_user_id()
        normalized_slug = slug.strip().lower()
        existing = (
            await self._session.execute(select(Tenant.id).where(Tenant.slug == normalized_slug))
        ).first()
        if existing is not None:
            raise ConflictError("tenant slug already taken")

        tenant = Tenant(
            id=new_uuid7(), name=name, slug=normalized_slug, owner_user_id=actor_id, status="active"
        )
        self._session.add(tenant)
        owner_role = await self._system_role(ROLE_OWNER)
        self._session.add(
            Membership(
                id=new_uuid7(),
                tenant_id=tenant.id,
                user_id=actor_id,
                role_id=owner_role.id,
                status="active",
            )
        )
        await self._session.flush()
        self.emit(
            "tenants.tenant.created",
            {"tenant_id": str(tenant.id), "name": name, "owner_user_id": str(actor_id)},
        )
        return _tenant_dto(tenant)

    async def accept_invitation(self, invitation_token: str) -> MembershipDTO:
        """Cross-tenant (maintenance UoW): join the inviting tenant."""
        actor_id = self._actor_user_id()
        token_hash = hash_token(invitation_token)
        invitation = (
            await self._session.execute(
                select(Invitation).where(Invitation.token_hash == token_hash)
            )
        ).scalar_one_or_none()
        if invitation is None or invitation.status != "pending":
            raise NotFoundError("invitation not found or not pending")
        if invitation.expires_at <= datetime.now(UTC):
            raise InvariantViolationError("invitation expired")

        already = (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.tenant_id == invitation.tenant_id, Membership.user_id == actor_id
                )
            )
        ).first()
        if already is not None:
            raise ConflictError("already a member of this tenant")

        membership = Membership(
            id=new_uuid7(),
            tenant_id=invitation.tenant_id,
            user_id=actor_id,
            role_id=invitation.role_id,
            status="active",
        )
        self._session.add(membership)
        invitation.status = "accepted"
        invitation.accepted_by_user_id = actor_id
        invitation.accepted_at = datetime.now(UTC)
        await self._session.flush()
        role_code = (await self._session.get(Role, invitation.role_id)).code  # type: ignore[union-attr]
        self.emit(
            "tenants.member.joined",
            {
                "tenant_id": str(invitation.tenant_id),
                "user_id": str(actor_id),
                "role": role_code,
            },
        )
        return _membership_dto(membership, role_code)

    # ---------------------------------------------------------- tenant-context
    async def update_tenant(
        self, *, name: str | None = None, default_locale: str | None = None
    ) -> TenantDTO:
        tenant = await self._session.get(Tenant, self.ctx.tenant_id)
        if tenant is None:
            raise NotFoundError("tenant not found")
        if name is not None:
            tenant.name = name
        if default_locale is not None:
            tenant.default_locale = default_locale
        await self._session.flush()
        return _tenant_dto(tenant)

    async def invite_member(self, email: str, role: str) -> tuple[InvitationDTO, str]:
        """Create an invitation. Returns (dto, raw_token). In Phase 3 the token
        is delivered via NotificationService instead of being returned."""
        role_row = await self._tenant_or_system_role(role)
        raw_token = new_opaque_token()
        invitation = Invitation(
            id=new_uuid7(),
            tenant_id=self.ctx.tenant_id,
            email=email.strip().lower(),
            role_id=role_row.id,
            token_hash=hash_token(raw_token),
            status="pending",
            invited_by_user_id=self._actor_user_id(),
            expires_at=datetime.now(UTC) + timedelta(days=INVITATION_TTL_DAYS),
        )
        repo = InvitationRepository(self._session, self.ctx)
        try:
            await repo.add(invitation)
        except Exception as exc:  # unique (tenant, lower(email)) WHERE pending
            raise ConflictError("a pending invitation for this email already exists") from exc
        self.emit(
            "tenants.member.invited",
            {
                "tenant_id": str(self.ctx.tenant_id),
                "invitation_id": str(invitation.id),
                "email": invitation.email,
                "role": role,
            },
        )
        dto = InvitationDTO(
            id=invitation.id,
            tenant_id=invitation.tenant_id,
            email=invitation.email,
            role_code=role,
            status=invitation.status,
            expires_at=invitation.expires_at,
        )
        return dto, raw_token

    async def revoke_invitation(self, invitation_id: UUID) -> None:
        repo = InvitationRepository(self._session, self.ctx)
        invitation = await repo.get_or_raise(invitation_id)
        if invitation.status != "pending":
            raise InvariantViolationError("only pending invitations can be revoked")
        invitation.status = "revoked"
        await self._session.flush()

    async def change_member_role(self, user_id: UUID, role: str) -> MembershipDTO:
        membership = await self._member_or_raise(user_id)
        new_role = await self._tenant_or_system_role(role)
        if new_role.code != ROLE_OWNER:
            await self._guard_last_owner(user_id, "demote")
        membership.role_id = new_role.id
        await self._session.flush()
        self.emit(
            "tenants.member.role_changed",
            {"tenant_id": str(self.ctx.tenant_id), "user_id": str(user_id), "role": role},
        )
        return _membership_dto(membership, new_role.code)

    async def remove_member(self, user_id: UUID) -> None:
        membership = await self._member_or_raise(user_id)
        await self._guard_last_owner(user_id, "remove")
        event_id = self.emit(
            "tenants.member.removed",
            {"tenant_id": str(self.ctx.tenant_id), "user_id": str(user_id)},
        )
        await self._session.delete(membership)
        await self._session.flush()
        # Critical action: also record directly, deduplicated by event_id (§3.5).
        from core.audit.service import AuditService

        await AuditService(self._session, self.ctx).record(
            action="tenants.member.removed",
            object_type="member",
            object_id=str(user_id),
            event_id=event_id,
        )

    # -------------------------------------------------------- platform-context
    async def set_status(self, tenant_id: UUID, status: str, reason: str | None = None) -> None:
        """Platform admin (maintenance UoW, ctx.tenant_id is None)."""
        if status not in ("active", "suspended"):
            raise InvariantViolationError("status must be 'active' or 'suspended'")
        tenant = await self._session.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError("tenant not found")
        tenant.status = status
        await self._session.flush()
        self.emit(
            "tenants.tenant.status_changed",
            {"tenant_id": str(tenant_id), "status": status, "reason": reason},
        )

    # ------------------------------------------------------------- internals
    def _actor_user_id(self) -> UUID:
        if self.ctx.actor.kind != "user" or not self.ctx.actor.id:
            raise InvariantViolationError("operation requires a user actor")
        return UUID(self.ctx.actor.id)

    async def _system_role(self, code: str) -> Role:
        role = (
            await self._session.execute(
                select(Role).where(Role.tenant_id.is_(None), Role.code == code)
            )
        ).scalar_one_or_none()
        if role is None:
            raise NotFoundError(f"system role {code!r} is not provisioned")
        return role

    async def _tenant_or_system_role(self, code: str) -> Role:
        role = (
            (
                await self._session.execute(
                    select(Role).where(
                        Role.code == code,
                        (Role.tenant_id == self.ctx.tenant_id) | Role.tenant_id.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if role is None:
            raise NotFoundError(f"role {code!r} not found")
        return role

    async def _member_or_raise(self, user_id: UUID) -> Membership:
        membership = (
            await self._session.execute(
                select(Membership).where(
                    Membership.tenant_id == self.ctx.tenant_id, Membership.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise NotFoundError("membership not found")
        return membership

    async def _guard_last_owner(self, user_id: UUID, _action: str) -> None:
        """Refuse to remove/demote the last owner of the tenant."""
        owner_role = await self._system_role(ROLE_OWNER)
        target = await self._member_or_raise(user_id)
        if target.role_id != owner_role.id:
            return
        owners = (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.tenant_id == self.ctx.tenant_id, Membership.role_id == owner_role.id
                )
            )
        ).all()
        if len(owners) <= 1:
            raise InvariantViolationError("cannot remove or demote the last owner")
