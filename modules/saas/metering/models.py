"""Usage-counter ORM model — owns saas_usage_counters (tenant-scoped, RLS).

One row per (tenant, metric, day): a pre-aggregated daily counter, incremented
by ``MeteringService.record`` via an atomic UPSERT. Day granularity keeps the
table small (no raw event log) while still answering period queries (sum of days)
and giving retention a natural cutoff. Internal to the feature: no sibling reads
this table — they call MeteringService (§1.2, the public interface).
"""

import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class UsageCounter(TimestampMixin, TenantScopedBase):
    __tablename__ = "saas_usage_counters"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[date] = mapped_column(Date, nullable=False)  # UTC day
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        Index(
            "uq_saas_usage_counters_tenant_id_metric_key_bucket",
            "tenant_id",
            "metric_key",
            "bucket",
            unique=True,
        ),
    )
