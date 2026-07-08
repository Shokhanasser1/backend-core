"""commerce_product_images.thumbnail_file_id (feature commerce.product_images).
Branch commerce_product_images.

Nullable link to a core/files thumbnail generated at attach time. No cross-table
FK (core/files is a separate component; integrity is enforced through FileService),
consistent with ``file_id`` on the same table.

Revision ID: commerce_product_images0002
Revises: commerce_product_images0001
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "commerce_product_images0002"
down_revision: str | Sequence[str] | None = "commerce_product_images0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "commerce_product_images",
        sa.Column("thumbnail_file_id", sa.Uuid(), nullable=True),  # no FK (core/files)
    )


def downgrade() -> None:
    op.drop_column("commerce_product_images", "thumbnail_file_id")
