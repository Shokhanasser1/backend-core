"""Public DTOs of commerce.orders (Pydantic frozen)."""

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrderItemDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: UUID
    quantity: int
    unit_price_amount: int
    currency: str


class OrderDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    status: str
    total_amount: int
    currency: str
    payment_id: UUID | None
    items: Sequence[OrderItemDTO]


class OrderCheckoutDTO(BaseModel):
    """Returned by place_order: the pending order + the payment checkout to pay it."""

    model_config = ConfigDict(frozen=True)

    order_id: UUID
    payment_id: UUID
    provider: str
    checkout_url: str


class OrderLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    quantity: int = Field(default=1, ge=1, le=1000)


class PlaceOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    items: Sequence[OrderLineIn] = Field(min_length=1, max_length=100)
