"""Event-bus deduplication table processed_events (schema doc §2.7).

Branch ``shared`` — service tables of the shared layer. DB roles and grants
are cluster-level objects provisioned with the core base migration in Phase 2
(schema doc §3.6); this table carries no business or tenant data.

Revision ID: shared0001
Revises: -
Create Date: 2026-07-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "shared0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("shared",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("handler", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("handler", "event_id", name="pk_processed_events"),
    )
    op.create_index("ix_processed_events_processed_at", "processed_events", ["processed_at"])


def downgrade() -> None:
    op.drop_index("ix_processed_events_processed_at", table_name="processed_events")
    op.drop_table("processed_events")
