"""Public DTOs of commerce.cart (Pydantic frozen)."""

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CartItemDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: UUID
    quantity: int
    unit_price_amount: int
    currency: str


class CartDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    status: str
    currency: str
    total_amount: int
    items: Sequence[CartItemDTO]


class AddItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    quantity: int = Field(default=1, ge=1, le=1000)
