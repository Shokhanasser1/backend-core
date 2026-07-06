"""Tenants ORM models (schema §2.2).

- ``tenants`` — the organization; global by nature (registry of tenants),
  RLS by id (a tenant sees itself and tenants it is a member of).
- ``memberships`` — user-tenant-role link; tenant-scoped.
- ``roles`` / ``role_permissions`` — hybrid: system rows (tenant_id NULL) +
  per-tenant custom rows.
- ``invitations`` — tenant-scoped.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, GlobalBase, TenantScopedBase, TimestampMixin
from shared.ids import new_uuid7


class Tenant(TimestampMixin, GlobalBase):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)  # lower; for URLs/subdomains
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    default_locale: Mapped[str] = mapped_column(Text, nullable=False, default="ru")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'deleted')", name="status"),
        Index("uq_tenants_slug", "slug", unique=True),
        Index("ix_tenants_owner_user_id", "owner_user_id"),
    )


class Membership(TimestampMixin, TenantScopedBase):
    __tablename__ = "memberships"

    # tenant_id + id come from TenantScopedBase.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="status"),
        Index("uq_memberships_tenant_id_user_id", "tenant_id", "user_id", unique=True),
        Index("ix_memberships_user_id", "user_id"),
        Index("ix_memberships_role_id", "role_id"),
        # FK to tenants (TenantScopedBase leaves tenant_id column bare).
        Index("ix_memberships_tenant_id", "tenant_id"),
    )


class Role(TimestampMixin, Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    # NULL = system role; otherwise a per-tenant custom role.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_system: Mapped[bool] = mapped_column(nullable=False, default=False)

    __table_args__ = (
        Index("ix_roles_tenant_id", "tenant_id"),
        # uq via UNIQUE NULLS NOT DISTINCT is added in the migration (PG16).
    )


class RolePermission(TimestampMixin, Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(Text, primary_key=True)


class Invitation(TimestampMixin, TenantScopedBase):
    __tablename__ = "invitations"

    email: Mapped[str] = mapped_column(Text, nullable=False)  # lower in the app
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'revoked', 'expired')", name="status"),
        Index("uq_invitations_token_hash", "token_hash", unique=True),
        Index("ix_invitations_tenant_id_status", "tenant_id", "status"),
        Index("ix_invitations_role_id", "role_id"),
    )
