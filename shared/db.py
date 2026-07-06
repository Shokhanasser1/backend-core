"""Declarative bases and DB conventions (schema doc §1.3).

- Deterministic constraint naming so Alembic autogenerate is stable.
- All timestamps are timezone-aware UTC (``timestamptz``).
- PK convention: UUIDv7 generated in the application.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from shared.ids import new_uuid7

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """created_at/updated_at convention; updated_at maintained by the app (no triggers)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


class GlobalBase(Base):
    """Base for global tables (no tenant_id): users, currencies, plans, service tables.

    Global tables are a sanctioned exception from tenant RLS (schema §1.2, OV-01);
    they are served by GlobalRepository which cannot touch tenant data.
    """

    __abstract__ = True


class TenantScopedBase(Base):
    """Base for tenant-scoped business tables: uuid7 PK + mandatory tenant_id.

    Concrete tables (Phase 2+) override ``tenant_id`` with an explicit
    FK -> tenants ON DELETE RESTRICT and add RLS via ``enable_tenant_rls``.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
