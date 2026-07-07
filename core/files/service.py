"""FileService — the public interface of core/files (interfaces: public services).

Validates uploads at the trust boundary (size cap + magic-bytes allowlist, the
client Content-Type is never trusted), stores the bytes in the configured backend
and keeps the tenant-scoped metadata row. Sibling features (commerce.product_images)
call this — they never read the ``files`` table. Events fire post-commit.
"""

import hashlib
from uuid import UUID

from core.files.content_types import sniff_content_type
from core.files.models import StoredFile
from core.files.ports import StoragePort
from core.files.repository import FileRepository
from core.files.schemas import FileDTO
from shared.config import Settings
from shared.context import TenantContext
from shared.errors import InvariantViolationError
from shared.events import EventBus
from shared.ids import new_uuid7
from shared.service import Service, UnitOfWork

_MAX_FILENAME_LENGTH = 255


def _to_dto(stored: StoredFile) -> FileDTO:
    return FileDTO.model_validate(stored)


def _sanitize_filename(name: str | None) -> str | None:
    """Keep only the basename, drop control/quote characters (header-injection and
    path-traversal safe), cap the length. None if nothing usable remains."""
    if not name:
        return None
    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in basename if ch.isprintable() and ch not in '"\r\n').strip()
    return cleaned[:_MAX_FILENAME_LENGTH] or None


class FileService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        storage: StoragePort,
        settings: Settings,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._repo = FileRepository(uow.session, ctx)
        self._storage = storage
        self._settings = settings

    @property
    def max_upload_bytes(self) -> int:
        return self._settings.files_max_upload_bytes

    async def upload(
        self, *, filename: str | None, declared_content_type: str | None, data: bytes
    ) -> FileDTO:
        """Validate (size + magic bytes), store the bytes, persist the metadata.

        ``declared_content_type`` (the client's claim) is intentionally ignored for
        the decision — the content type is sniffed from the bytes and checked
        against the allowlist."""
        if not data:
            raise InvariantViolationError("uploaded file is empty")
        if len(data) > self._settings.files_max_upload_bytes:
            raise InvariantViolationError("uploaded file exceeds the maximum allowed size")
        content_type = sniff_content_type(data)
        allowed = self._settings.files_allowed_content_type_list
        if content_type is None or content_type not in allowed:
            raise InvariantViolationError("unsupported file type (allowed: images)")

        tenant_id = self.ctx.tenant_id
        if tenant_id is None:  # pragma: no cover - the repository already guards this
            raise InvariantViolationError("file upload requires a tenant context")

        file_id = new_uuid7()
        storage_key = f"{tenant_id}/{file_id}"
        stored = StoredFile(
            id=file_id,
            storage_key=storage_key,
            content_type=content_type,
            byte_size=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            original_filename=_sanitize_filename(filename),
        )
        await self._repo.add(stored)  # stamps tenant_id, flushes
        # Store bytes last: any failure rolls the row back with it. A crash between
        # this put and commit can orphan an object (documented; a GC sweep is backlog).
        await self._storage.put(storage_key, data, content_type=content_type)
        self.emit(
            "files.file.uploaded",
            {"file_id": str(file_id), "content_type": content_type, "byte_size": len(data)},
        )
        return _to_dto(stored)

    async def get(self, file_id: UUID) -> FileDTO:
        """Metadata only (404 for a foreign/missing file — RLS + repository)."""
        return _to_dto(await self._repo.get_or_raise(file_id))

    async def open(self, file_id: UUID) -> tuple[FileDTO, bytes]:
        """Metadata + bytes, for streaming a download."""
        stored = await self._repo.get_or_raise(file_id)
        data = await self._storage.get(stored.storage_key)
        return _to_dto(stored), data

    async def delete(self, file_id: UUID) -> None:
        stored = await self._repo.get_or_raise(file_id)
        await self._repo.delete(stored)
        await self._session.flush()
        # After the row is gone: if this fails the transaction rolls back and the
        # row is restored (delete is idempotent, so a retry is safe).
        await self._storage.delete(stored.storage_key)
        self.emit("files.file.deleted", {"file_id": str(file_id)})
