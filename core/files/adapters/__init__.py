"""Storage adapter registry — select the backend by config (fail loud at startup).

Mirrors billing's ``build_payment_providers``: a misconfigured backend raises here,
during lifespan, not on the first upload. The S3 adapter (and boto3) is imported
lazily so a filesystem-only deployment never pulls it in.
"""

import tempfile
from pathlib import Path

from core.files.adapters.filesystem import FilesystemStorage
from core.files.ports import StoragePort
from shared.config import Settings
from shared.errors import InvariantViolationError


def _default_filesystem_root() -> str:
    return str(Path(tempfile.gettempdir()) / "backend-core-files")


def build_storage(settings: Settings) -> StoragePort:
    """The configured storage backend. Filesystem for dev/test (no external
    service), S3-compatible for prod (credentials required — else fails here)."""
    backend = settings.files_storage_backend
    if backend == "filesystem":
        return FilesystemStorage(settings.files_filesystem_root or _default_filesystem_root())
    if backend == "s3":
        # Lazy import: boto3 is only needed when the S3 backend is actually used.
        from core.files.adapters.s3 import S3Storage

        return S3Storage.from_settings(settings)
    raise InvariantViolationError(f"unknown files storage backend: {backend!r}")
