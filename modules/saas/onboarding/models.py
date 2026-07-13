"""Onboarding-progress ORM model — owns saas_onboarding_progress (tenant, RLS).

One row per completed step for a tenant (absence = not yet completed). The set of
steps that make up the checklist is configuration (SAAS_ONBOARDING_STEPS), not a
column here — so a client defines its own activation journey without a migration.
Internal to the feature: no sibling reads this table — they call OnboardingService
(§1.2, the public interface).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class OnboardingProgress(TimestampMixin, TenantScopedBase):
    __tablename__ = "saas_onboarding_progress"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_saas_onboarding_progress_tenant_id_step_key",
            "tenant_id",
            "step_key",
            unique=True,
        ),
    )
