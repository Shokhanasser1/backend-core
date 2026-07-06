"""Public DTOs of core/tenants (Pydantic frozen)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class TenantDTO(_Frozen):
    id: UUID
    name: str
    slug: str
    status: str
    default_locale: str


class MembershipDTO(_Frozen):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    role_id: UUID
    role_code: str
    status: str


class InvitationDTO(_Frozen):
    id: UUID
    tenant_id: UUID
    email: str
    role_code: str
    status: str
    expires_at: datetime


# --- request bodies ---


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTenantIn(_StrictIn):
    name: str
    slug: str


class UpdateTenantIn(_StrictIn):
    name: str | None = None
    default_locale: str | None = None


class InviteMemberIn(_StrictIn):
    email: str
    role: str


class ChangeMemberRoleIn(_StrictIn):
    role: str


class AcceptInvitationIn(_StrictIn):
    invitation_token: str
