"""Billing tables: currencies, plans, subscriptions, payments, payment_webhooks.

Branch ``core_billing`` (schema §2.3). Financial tables (subscriptions,
payments) get no DELETE grant — history is immutable. payment_webhooks is
hybrid (written before the tenant is known). Seeds currencies (UZS/USD) and a
default free plan for auto-subscription (OV-21).

Revision ID: core_billing0001
Revises: -
Create Date: 2026-07-06
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_USER
from shared.ids import new_uuid7

revision: str = "core_billing0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("core_billing",)
depends_on: str | Sequence[str] | None = ("core_tenants0001", "shared0002")

_TABLES = ("payment_webhooks", "payments", "subscriptions", "plans", "currencies")


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    _create_tables()
    _apply_rls()
    _seed()


def _create_tables() -> None:
    op.create_table(
        "currencies",
        sa.Column("code", sa.String(3), nullable=False),
        sa.Column("exponent", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("exponent BETWEEN 0 AND 4", name="ck_currencies_exponent_range"),
        sa.PrimaryKeyConstraint("code", name="pk_currencies"),
    )

    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", JSONB(), nullable=False),
        sa.Column("description", JSONB(), nullable=True),
        sa.Column("price_amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="UZS"),
        sa.Column("period", sa.Text(), nullable=False, server_default="month"),
        sa.Column("trial_days", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.CheckConstraint("price_amount >= 0", name="ck_plans_price_non_negative"),
        sa.CheckConstraint("period IN ('month', 'year')", name="ck_plans_period"),
        sa.ForeignKeyConstraint(
            ["currency"],
            ["currencies.code"],
            name="fk_plans_currency_currencies",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_plans"),
    )
    op.create_index("uq_plans_code", "plans", ["code"], unique=True)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("price_amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending','trialing','active','past_due','canceled','expired')",
            name="ck_subscriptions_status",
        ),
        sa.CheckConstraint("price_amount >= 0", name="ck_subscriptions_price_non_negative"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_subscriptions_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plans.id"], name="fk_subscriptions_plan_id_plans", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["currency"],
            ["currencies.code"],
            name="fk_subscriptions_currency_currencies",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_subscriptions"),
    )
    op.create_index(
        "uq_subscriptions_one_live_per_tenant",
        "subscriptions",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','trialing','active','past_due')"),
    )
    op.create_index(
        "ix_subscriptions_tenant_id_created_at", "subscriptions", ["tenant_id", "created_at"]
    )
    op.create_index("ix_subscriptions_plan_id", "subscriptions", ["plan_id"])
    op.create_index(
        "ix_subscriptions_status_current_period_end",
        "subscriptions",
        ["status", "current_period_end"],
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="UZS"),
        sa.Column("status", sa.Text(), nullable=False, server_default="created"),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_transaction_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('created','pending','succeeded','failed','canceled','expired')",
            name="ck_payments_status",
        ),
        sa.CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_payments_tenant_id_tenants", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name="fk_payments_subscription_id_subscriptions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["currency"],
            ["currencies.code"],
            name="fk_payments_currency_currencies",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
    )
    op.create_index(
        "uq_payments_tenant_id_idempotency_key",
        "payments",
        ["tenant_id", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "uq_payments_provider_provider_transaction_id",
        "payments",
        ["provider", "provider_transaction_id"],
        unique=True,
        postgresql_where=sa.text("provider_transaction_id IS NOT NULL"),
    )
    op.create_index("ix_payments_tenant_id_created_at", "payments", ["tenant_id", "created_at"])
    op.create_index("ix_payments_subscription_id", "payments", ["subscription_id"])
    op.create_index(
        "ix_payments_tenant_id_purpose_reference", "payments", ["tenant_id", "purpose", "reference"]
    )
    op.create_index(
        "ix_payments_live",
        "payments",
        ["created_at"],
        postgresql_where=sa.text("status IN ('created','pending')"),
    )

    op.create_table(
        "payment_webhooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("payment_id", sa.Uuid(), nullable=True),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("headers", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("signature_valid", sa.Boolean(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="received"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('received','processed','rejected','failed')",
            name="ck_payment_webhooks_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_payment_webhooks_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name="fk_payment_webhooks_payment_id_payments",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payment_webhooks"),
    )
    op.create_index(
        "uq_payment_webhooks_provider_dedup_key",
        "payment_webhooks",
        ["provider", "dedup_key"],
        unique=True,
    )
    op.create_index(
        "ix_payment_webhooks_unprocessed",
        "payment_webhooks",
        ["created_at"],
        postgresql_where=sa.text("status IN ('received','failed')"),
    )
    op.create_index("ix_payment_webhooks_payment_id", "payment_webhooks", ["payment_id"])
    op.create_index("ix_payment_webhooks_tenant_id", "payment_webhooks", ["tenant_id"])


def _financial_rls(table: str) -> None:
    """Tenant isolation without a DELETE grant (financial history is immutable)."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL TO {ROLE_USER} "
        f"USING (tenant_id = app_current_tenant_id()) "
        f"WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        f"CREATE POLICY maintenance_all ON {table} FOR ALL TO {ROLE_MAINTENANCE} "
        f"USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {ROLE_MAINTENANCE}")


def _apply_rls() -> None:
    # Global reference tables: read-only for runtime roles.
    op.execute(f"GRANT SELECT ON currencies TO {ROLE_USER}, {ROLE_MAINTENANCE}")
    op.execute(f"GRANT SELECT ON plans TO {ROLE_USER}, {ROLE_MAINTENANCE}")

    _financial_rls("subscriptions")
    _financial_rls("payments")

    # payment_webhooks: written system-context (app_maintenance) before the
    # tenant is known; app_user only reads its own tenant's rows.
    op.execute("ALTER TABLE payment_webhooks ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_read ON payment_webhooks FOR SELECT TO app_user "
        "USING (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        "CREATE POLICY maintenance_all ON payment_webhooks FOR ALL TO app_maintenance "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT ON payment_webhooks TO {ROLE_USER}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON payment_webhooks TO {ROLE_MAINTENANCE}")


def _seed() -> None:
    op.execute(
        "INSERT INTO currencies (code, exponent, name) VALUES "
        "('UZS', 0, 'Uzbekistani Som'), ('USD', 2, 'US Dollar')"
    )
    # Default free plan for auto-subscription (OV-21). Uses a fixed UUID so the
    # downgrade removes exactly it.
    free_id = new_uuid7()
    op.execute(
        sa.text(
            "INSERT INTO plans "
            "(id, code, name, price_amount, currency, period, trial_days, is_active) "
            "VALUES (:id, 'free', CAST(:name AS jsonb), 0, 'UZS', 'month', 0, true)"
        ).bindparams(
            id=free_id,
            name='{"ru": "Бесплатный", "uz": "Bepul"}',
        )
    )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {ROLE_USER}")
        op.execute(f"REVOKE ALL ON {table} FROM {ROLE_MAINTENANCE}")
    op.drop_table("payment_webhooks")
    op.drop_table("payments")
    op.drop_table("subscriptions")
    op.drop_table("plans")
    op.drop_table("currencies")
