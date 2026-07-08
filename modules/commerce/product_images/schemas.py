"""Public DTOs of commerce.product_images (Pydantic frozen)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProductImageDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    product_id: UUID
    file_id: UUID
    thumbnail_file_id: UUID | None
    position: int
    alt_text: str | None
