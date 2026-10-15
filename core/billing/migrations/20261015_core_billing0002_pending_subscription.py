"""A paid checkout lives ALONGSIDE the live subscription (upgrade from free).

Branch ``core_billing``. Revision 0001 counted ``pending`` as live, so a tenant on
the free plan could never open a paid checkout (the second row violated
``uq_subscriptions_one_live_per_tenant``). Now: at most one live subscription
(trialing/active/past_due) AND at most one pending checkout per tenant; payment
success swaps them in one transaction (core/billing/service.py).

Revision ID: core_billing0002
Revises: core_billing0001
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "core_billing0002"
down_revision: str | Sequence[str] | None = "core_billing0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_subscriptions_one_live_per_tenant", table_name="subscriptions")
    op.create_index(
        "uq_subscriptions_one_live_per_tenant",
        "subscriptions",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('trialing','active','past_due')"),
    )
    op.create_index(
        "uq_subscriptions_one_pending_per_tenant",
        "subscriptions",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_subscriptions_one_pending_per_tenant", table_name="subscriptions")
    op.drop_index("uq_subscriptions_one_live_per_tenant", table_name="subscriptions")
    op.create_index(
        "uq_subscriptions_one_live_per_tenant",
        "subscriptions",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','trialing','active','past_due')"),
    )
