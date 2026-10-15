"""One active cart per buyer per shop (feature commerce.cart). Branch commerce_cart.

The 0001 index on (tenant_id, customer_user_id) was not unique, so two concurrent
first "add to cart" requests could each open an active cart. Older duplicates (if
any exist) are closed as ``abandoned`` — a new terminal status — before the
partial unique index is created, so the migration never fails on real data.

Revision ID: commerce_cart0002
Revises: commerce_cart0001
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "commerce_cart0002"
down_revision: str | Sequence[str] | None = "commerce_cart0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_commerce_carts_status", "commerce_carts", type_="check")
    op.create_check_constraint(
        "ck_commerce_carts_status",
        "commerce_carts",
        "status IN ('active', 'checked_out', 'abandoned')",
    )
    op.execute(
        "UPDATE commerce_carts c SET status = 'abandoned' "
        "WHERE c.status = 'active' AND EXISTS ("
        "SELECT 1 FROM commerce_carts newer "
        "WHERE newer.tenant_id = c.tenant_id "
        "AND newer.customer_user_id = c.customer_user_id "
        "AND newer.status = 'active' "
        "AND (newer.created_at, newer.id) > (c.created_at, c.id))"
    )
    op.create_index(
        "uq_commerce_carts_one_active_per_customer",
        "commerce_carts",
        ["tenant_id", "customer_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_commerce_carts_one_active_per_customer", table_name="commerce_carts")
    op.execute("UPDATE commerce_carts SET status = 'checked_out' WHERE status = 'abandoned'")
    op.drop_constraint("ck_commerce_carts_status", "commerce_carts", type_="check")
    op.create_check_constraint(
        "ck_commerce_carts_status", "commerce_carts", "status IN ('active', 'checked_out')"
    )
