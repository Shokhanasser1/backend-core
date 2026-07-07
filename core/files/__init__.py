"""core/files — tenant-scoped object storage (uploads with magic-bytes validation).

A core module (always available, wired in app/main.py — not a pluggable feature).
The file BYTES live in a storage backend (filesystem in dev/test, S3-compatible in
prod) behind ``StoragePort``; only the tenant-scoped metadata row lives in Postgres
(table ``files``, RLS). ``FileService`` is the public interface other modules and
features build on (e.g. commerce.product_images) — never the ``files`` table.
"""

from core.files.schemas import FileDTO
from core.files.service import FileService

# Public interface of the module: downstream features import FileService/FileDTO
# from here (the package), never the internal service module.
__all__ = ["FileDTO", "FileService"]
