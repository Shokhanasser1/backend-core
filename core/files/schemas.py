"""Public DTO of core/files (Pydantic frozen). The ORM never crosses the boundary."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FileDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    content_type: str
    byte_size: int
    checksum_sha256: str
    original_filename: str | None
