"""Public DTOs of core/billing (interfaces §3.3). ORM never crosses the boundary."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer

from shared.money import Money

PaymentStatus = Literal["created", "pending", "succeeded", "failed", "canceled", "expired"]


def _money_json(value: Money) -> dict[str, object]:
    """Money is an arbitrary (non-Pydantic) type, so DTOs that expose it over HTTP
    serialize it explicitly as {amount, currency}."""
    return {"amount": value.amount, "currency": value.currency}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


class PlanDTO(_Frozen):
    id: UUID
    code: str
    name: dict[str, Any]
    price: Money
    period: str
    trial_days: int

    _ser_price = field_serializer("price")(_money_json)


class SubscriptionDTO(_Frozen):
    id: UUID
    plan_code: str
    status: str
    current_period_end: datetime
    cancel_at_period_end: bool


class PaymentProviderInfo(_Frozen):
    code: str
    title_key: str
    enabled: bool


class PaymentDTO(_Frozen):
    id: UUID
    status: PaymentStatus
    amount: Money
    purpose: str
    reference: str
    provider: str
    paid_at: datetime | None

    _ser_amount = field_serializer("amount")(_money_json)


class CheckoutDTO(_Frozen):
    payment_id: UUID
    provider: str
    checkout_url: str
    expires_at: datetime | None


# --- request bodies ---


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartSubscriptionIn(_StrictIn):
    plan_code: str
    provider: str
