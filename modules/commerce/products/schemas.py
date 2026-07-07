"""Public DTOs of commerce.products (Pydantic frozen)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProductDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    sku: str
    name: str
    description: str | None
    price_amount: int  # minor units of `currency`
    currency: str
    status: str


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProductIn(_StrictIn):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price_amount: int = Field(ge=0)
    currency: str = Field(default="UZS", min_length=3, max_length=3)


class UpdateProductIn(_StrictIn):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price_amount: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
