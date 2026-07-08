"""Entitlement ORM models — owns saas_plan_entitlements + saas_tenant_entitlements.

Two tables, two scopes:

- ``saas_plan_entitlements`` is a GLOBAL reference table (like billing's plans /
  currencies, schema §1.2): the product's tariff grid — which feature flags and
  numeric limits each plan_code grants. Read-only at runtime; a client project
  seeds it by migration. ``plan_code`` is the bare billing plan code (no
  cross-module FK — reading billing's tables is forbidden; the code is validated
  by the presence of a matching row, not a constraint).
- ``saas_tenant_entitlements`` is TENANT-scoped (RLS): the tenant's currently
  active plan, maintained by the bus subscribers reacting to
  billing.subscription.activated/canceled. One row per tenant.

Internal to the feature: no sibling reads these tables — they call
EntitlementService (§1.2, the public interface).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import GlobalBase, TenantScopedBase, TimestampMixin


class PlanEntitlement(TimestampMixin, GlobalBase):
    """One entitlement a plan grants: a boolean feature flag or a numeric limit.

    ``kind`` discriminates the value column: 'flag' uses ``bool_value``, 'limit'
    uses ``int_value`` (NULL = unlimited). The check constraints keep the row
    internally consistent so a malformed grid cannot be inserted.
    """

    __tablename__ = "saas_plan_entitlements"

    plan_code: Mapped[str] = mapped_column(Text, primary_key=True)
    entitlement_key: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # 'flag' | 'limit'
    bool_value: Mapped[bool | None] = mapped_column(Boolean)
    int_value: Mapped[int | None] = mapped_column(BigInteger)  # NULL = unlimited

    __table_args__ = (
        CheckConstraint("kind IN ('flag', 'limit')", name="kind"),
        CheckConstraint(
            "(kind = 'flag' AND bool_value IS NOT NULL AND int_value IS NULL) OR "
            "(kind = 'limit' AND bool_value IS NULL)",
            name="value_matches_kind",
        ),
        CheckConstraint("int_value IS NULL OR int_value >= 0", name="limit_non_negative"),
    )


class TenantEntitlement(TimestampMixin, TenantScopedBase):
    """The tenant's active plan snapshot (one row per tenant).

    ``current_period_end`` comes from the activation event; ``canceled`` is set
    when billing signals a cancel-at-period-end. Effective coverage lapses only
    once a canceled subscription is past its period end (EntitlementService).
    """

    __tablename__ = "saas_tenant_entitlements"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    plan_code: Mapped[str] = mapped_column(Text, nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("uq_saas_tenant_entitlements_tenant_id", "tenant_id", unique=True),)
