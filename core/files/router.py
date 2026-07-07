"""File endpoints (/api/files). Every route carries exactly one permission marker
(§5.2). Uploads validate magic bytes + size in the service; downloads stream the
bytes back inline. Only allowlisted raster images are ever stored and the global
security-headers middleware adds ``X-Content-Type-Options: nosniff``, so inline
serving is XSS-safe.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import Response

from core.auth.deps import require_permission
from core.files import permissions as perms
from core.files.deps import file_service
from core.files.schemas import FileDTO
from core.files.service import FileService

router = APIRouter(prefix="/api/files", tags=["files"])


def _content_disposition(filename: str | None) -> str:
    """inline disposition with a conservative ASCII-safe filename (or none) — no
    header injection, no non-latin1 bytes in the header."""
    allowed = "._- "
    safe = "".join(
        ch for ch in (filename or "") if ch.isascii() and (ch.isalnum() or ch in allowed)
    )
    if safe:
        return f'inline; filename="{safe}"'
    return "inline"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perms.FILE_UPLOAD))],
)
async def upload_file(
    upload: UploadFile = File(...), service: FileService = Depends(file_service)
) -> FileDTO:
    # Read at most cap+1 bytes: an oversized upload is rejected without buffering
    # the whole payload; the service enforces the exact limit and validates content.
    data = await upload.read(service.max_upload_bytes + 1)
    return await service.upload(
        filename=upload.filename, declared_content_type=upload.content_type, data=data
    )


@router.get("/{file_id}/meta", dependencies=[Depends(require_permission(perms.FILE_READ))])
async def get_file_meta(file_id: UUID, service: FileService = Depends(file_service)) -> FileDTO:
    return await service.get(file_id)


@router.get("/{file_id}", dependencies=[Depends(require_permission(perms.FILE_READ))])
async def download_file(file_id: UUID, service: FileService = Depends(file_service)) -> Response:
    dto, data = await service.open(file_id)
    return Response(
        content=data,
        media_type=dto.content_type,
        headers={"Content-Disposition": _content_disposition(dto.original_filename)},
    )


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perms.FILE_DELETE))],
)
async def delete_file(file_id: UUID, service: FileService = Depends(file_service)) -> None:
    await service.delete(file_id)
