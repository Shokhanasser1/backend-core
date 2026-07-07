"""StoredFile ORM model — owns the ``files`` table (tenant-scoped, RLS).

Internal to core/files: no other module/feature reads this table (ADR-0005) —
they go through FileService. The bytes live in the storage backend under
``storage_key``; this row is the tenant-scoped metadata + integrity checksum.
"""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import TenantScopedBase, TimestampMixin


class StoredFile(TimestampMixin, TenantScopedBase):
    __tablename__ = "files"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    # Opaque key in the storage backend; unique per tenant. Generated as
    # "<tenant_id>/<file_id>" so a backend-level mishap cannot cross tenants.
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)  # sniffed, allowlisted
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text)  # sanitized; display only

    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="byte_size_non_negative"),
        Index("ix_files_tenant_id", "tenant_id"),
        Index("uq_files_tenant_id_storage_key", "tenant_id", "storage_key", unique=True),
    )
