"""PaymentProvider port (interfaces §4.1).

UZ providers mostly call US (merchant callbacks), so the port is symmetric:
an outgoing create_checkout plus normalization of incoming callbacks. Any
callback outcome is answered in the provider's own dialect via
build_webhook_response — including the correct answer to a repeated delivery.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol

from core.billing.schemas import CheckoutDTO, PaymentDTO
from shared.money import Money

CallbackAction = Literal["check", "create", "confirm", "cancel", "status"]
OutcomeStatus = Literal[
    "ok",
    "already_processed",
    "invalid_signature",
    "not_found",
    "amount_mismatch",
    "invalid_state",
]


@dataclass(frozen=True, slots=True)
class RawWebhook:
    headers: Mapping[str, str]
    body: str  # raw request body as received
    form: Mapping[str, str] = field(default_factory=dict)  # parsed form (Click)


@dataclass(frozen=True, slots=True)
class ProviderCallback:
    provider: str
    provider_txn_id: str | None  # None only for action="check" (Payme)
    action: CallbackAction
    payment_reference: str  # our payment_id / merchant order id
    amount: Money
    raw: Mapping[str, Any]
    # A recognized-but-unauthenticated callback is normal adversarial traffic,
    # not an exception: parse_webhook returns it with signature_valid=False so the
    # caller answers uniformly in the provider dialect (invalid_signature outcome).
    # WebhookVerificationError is reserved for unrecognizable requests (no dialect).
    signature_valid: bool = True


@dataclass(frozen=True, slots=True)
class CallbackOutcome:
    status: OutcomeStatus
    payment: PaymentDTO | None = None
    detail: str | None = None
    # The originating callback: build_webhook_response needs request context to
    # answer in the provider dialect (e.g. echo the Payme JSON-RPC request id).
    callback: ProviderCallback | None = None


@dataclass(frozen=True, slots=True)
class WebhookResponse:
    status_code: int
    body: Mapping[str, Any]


class PaymentProvider(Protocol):
    code: ClassVar[str]  # "payme" | "click"

    async def create_checkout(self, payment: PaymentDTO, return_url: str | None) -> CheckoutDTO: ...

    def parse_webhook(self, raw: RawWebhook) -> ProviderCallback:
        """Verify and normalize a callback. A recognized request with a bad
        signature/auth is returned with signature_valid=False (the caller answers
        in the provider dialect). Only an unrecognizable request — where no
        dialect response is possible — raises WebhookVerificationError."""
        ...

    def build_webhook_response(self, outcome: CallbackOutcome) -> WebhookResponse:
        """Provider-dialect response for ANY outcome (success, error, replay)."""
        ...
