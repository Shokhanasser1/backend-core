"""Contacts ORM models — own crm_companies + crm_contacts (tenant-scoped, RLS).

Two entities of one feature: a company (organization) and a contact (person) who
optionally belongs to a company. The company_id FK is intra-feature (both tables
are ours) with ON DELETE SET NULL, so deleting a company un-assigns its contacts
rather than blocking. Internal to the feature: siblings (deals, tasks — later)
read people/companies through ContactsService (§1.2), never these tables.
"""

import uuid

from sqlalchemy import ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class Company(TimestampMixin, TenantScopedBase):
    __tablename__ = "crm_companies"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    website: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_crm_companies_tenant_id_name", "tenant_id", "name"),)


class Contact(TimestampMixin, TenantScopedBase):
    __tablename__ = "crm_contacts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    # Optional link to a company in the SAME tenant. SET NULL on company delete
    # (un-assign, not block). The service validates the id via the tenant-scoped
    # CompanyRepository, so a cross-tenant id is a 404 before it ever reaches here.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("crm_companies.id", ondelete="SET NULL")
    )
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    position: Mapped[str | None] = mapped_column(Text)  # job title
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_crm_contacts_tenant_id_company_id", "tenant_id", "company_id"),)
