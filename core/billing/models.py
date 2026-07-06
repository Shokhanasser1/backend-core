"""Billing ORM models (schema §2.3).

- currencies, plans — global (installation-level products / reference).
- subscriptions, payments — tenant-scoped (financial history, no DELETE).
- payment_webhooks — hybrid (written before the tenant is known).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, GlobalBase, TenantScopedBase, TimestampMixin
from shared.ids import new_uuid7


class Currency(TimestampMixin, GlobalBase):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)  # ISO 4217
    exponent: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # UZS=0, USD=2
    name: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (CheckConstraint("exponent BETWEEN 0 AND 4", name="exponent_range"),)


class Plan(TimestampMixin, GlobalBase):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # {"ru":..,"uz":..}
    description: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    price_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # minor units
    currency: Mapped[str] = mapped_column(
        String(3), ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False, default="UZS"
    )
    period: Mapped[str] = mapped_column(Text, nullable=False, default="month")
    trial_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint("price_amount >= 0", name="price_non_negative"),
        CheckConstraint("period IN ('month', 'year')", name="period"),
        Index("uq_plans_code", "code", unique=True),
    )


class Subscription(TimestampMixin, TenantScopedBase):
    __tablename__ = "subscriptions"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    price_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # snapshot
    currency: Mapped[str] = mapped_column(
        String(3), ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False
    )
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','trialing','active','past_due','canceled','expired')",
            name="status",
        ),
        CheckConstraint("price_amount >= 0", name="price_non_negative"),
        Index("ix_subscriptions_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_subscriptions_plan_id", "plan_id"),
        Index("ix_subscriptions_status_current_period_end", "status", "current_period_end"),
        Index("ix_subscriptions_tenant_id", "tenant_id"),
    )


class Payment(TimestampMixin, TenantScopedBase):
    __tablename__ = "payments"

    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("subscriptions.id", ondelete="RESTRICT")
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False, default="UZS"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="created")
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('created','pending','succeeded','failed','canceled','expired')",
            name="status",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index(
            "uq_payments_tenant_id_idempotency_key",
            "tenant_id",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_payments_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_payments_subscription_id", "subscription_id"),
        Index("ix_payments_tenant_id_purpose_reference", "tenant_id", "purpose", "reference"),
        Index("ix_payments_tenant_id", "tenant_id"),
    )


class PaymentWebhook(TimestampMixin, Base):
    __tablename__ = "payment_webhooks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # FK added in migration
    payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    raw_body: Mapped[str] = mapped_column(Text, nullable=False)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    signature_valid: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="received")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('received','processed','rejected','failed')", name="status"),
        Index("uq_payment_webhooks_provider_dedup_key", "provider", "dedup_key", unique=True),
        Index("ix_payment_webhooks_payment_id", "payment_id"),
        Index("ix_payment_webhooks_tenant_id", "tenant_id"),
    )
