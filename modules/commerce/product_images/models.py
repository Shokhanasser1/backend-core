"""ProductImage ORM model — owns commerce_product_images (tenant-scoped, RLS).

Internal to the feature. ``product_id`` (commerce.products) and ``file_id``
(core/files) are bare UUIDs with NO cross-table FK — those tables belong to other
components and are validated through their public services (ProductService,
FileService), never read directly (ADR-0005).
"""

import uuid

from sqlalchemy import ForeignKey, Index, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class ProductImage(TimestampMixin, TenantScopedBase):
    __tablename__ = "commerce_product_images"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # no FK (sibling feature)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # no FK (core/files)
    # Resized variant generated at attach time; NULL if none. No FK (core/files).
    thumbnail_file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alt_text: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_commerce_product_images_tenant_id_product_id", "tenant_id", "product_id"),
    )
