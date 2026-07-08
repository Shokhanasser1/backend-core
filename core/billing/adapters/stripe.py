"""Stripe Checkout adapter (interfaces §4.1).

The third payment provider, aimed at international clients (Payme/Click cover
UZS). Unlike the UZ providers — which we drive by handing the payer a
pre-built URL and then receive merchant callbacks — Stripe needs a
**server-to-server** call to create a Checkout Session (``create_checkout``
POSTs to the Stripe API and returns the hosted ``url``). Inbound events arrive
as signed webhooks.

Amounts map **1:1** to our ledger minor units: Stripe expresses amounts in the
currency's smallest unit (cents for USD, yen for JPY), which is exactly our
``Money.amount`` convention per currency. So there is no ``x100`` conversion
here (that is a Payme-only quirk); the only rule is that the paid amount must
equal the amount we recorded — the webhook processor reconciles it.

Webhook authenticity is the Stripe ``Stripe-Signature`` header: an HMAC-SHA256
over ``"{timestamp}.{raw_body}"`` keyed by the endpoint signing secret,
compared in constant time. The timestamp is part of the signed payload (so it
cannot be tampered), and effectively-once delivery is guaranteed by the webhook
ledger keyed on Stripe's unique event id — so, like the sibling adapters, this
one does not add a wall-clock replay window (which would make a pure function
time-dependent); replay defence lives in ``WebhookProcessor``.

Event mapping onto the shared port's action model:
- ``checkout.session.completed`` (``payment_status == "paid"``) -> ``confirm``
  -> ``mark_succeeded``;
- ``checkout.session.expired`` -> ``cancel`` -> ``mark_canceled_by_provider``;
- any other authenticated, well-formed event -> the read-only ``status`` action
  (a no-op ack): Stripe delivers every subscribed event type to one endpoint
  and only inspects the HTTP status code, so we acknowledge with 200 and change
  nothing. A bad signature is answered 400; an unparsable request has no dialect
  and falls back to the processor's ``WebhookVerificationError`` (403).
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx

from core.billing.ports import (
    CallbackAction,
    CallbackOutcome,
    ProviderCallback,
    RawWebhook,
    WebhookResponse,
)
from core.billing.schemas import CheckoutDTO, PaymentDTO
from shared.config import Settings
from shared.errors import PaymentProviderError, WebhookVerificationError
from shared.money import Money
from shared.resilience import CircuitBreaker, RetryPolicy, call_resilient

# Stripe event types we act on (everything else is acknowledged as a no-op).
EVENT_COMPLETED = "checkout.session.completed"
EVENT_EXPIRED = "checkout.session.expired"

_TIMEOUT = 20.0


class _StripeTransientError(PaymentProviderError):
    """5xx / 429 / network from the Stripe API -> retry. A 4xx (bad request,
    auth) is raised as the base PaymentProviderError instead, which is NOT in the
    retry ``transient`` set below, so it fails fast."""


# Transient set for create_checkout: our transient marker plus low-level network
# errors. The base PaymentProviderError (permanent 4xx) is intentionally absent.
_CHECKOUT_TRANSIENT: tuple[type[Exception], ...] = (
    _StripeTransientError,
    TimeoutError,
    ConnectionError,
    OSError,
)


def to_stripe_amount(money: Money) -> int:
    """Ledger minor units -> Stripe amount (same smallest-unit convention, 1:1)."""
    return money.amount


def minor_units_from_stripe(amount: Any, currency: Any) -> Money:
    """Stripe amount (smallest unit) -> ledger Money (1:1). Rejects a non-integer
    or negative amount; the currency is upper-cased to our ISO 4217 convention."""
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise PaymentProviderError(f"invalid Stripe amount: {amount!r}")
    return Money(amount, _normalize_currency(currency))


def _normalize_currency(currency: Any) -> str:
    code = str(currency or "").upper()
    if len(code) != 3 or not code.isalpha():
        raise PaymentProviderError(f"invalid Stripe currency: {currency!r}")
    return code


class StripeProvider:
    code: ClassVar[str] = "stripe"
    api_base_url: ClassVar[str] = "https://api.stripe.com"
    checkout_path: ClassVar[str] = "/v1/checkout/sessions"

    def __init__(
        self,
        *,
        secret_key: str,
        webhook_secret: str,
        success_url: str,
        cancel_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not secret_key or not webhook_secret:
            raise PaymentProviderError("Stripe adapter requires secret_key and webhook_secret")
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret
        self._success_url = success_url
        self._cancel_url = cancel_url
        self._transport = transport  # tests inject httpx.MockTransport
        self._breaker = CircuitBreaker(name="stripe", failure_threshold=5, recovery_time=30.0)

    @classmethod
    def from_settings(cls, settings: Settings) -> "StripeProvider":
        return cls(
            secret_key=settings.stripe_secret_key,
            webhook_secret=settings.stripe_webhook_secret,
            success_url=settings.stripe_success_url,
            cancel_url=settings.stripe_cancel_url,
        )

    async def create_checkout(self, payment: PaymentDTO, return_url: str | None) -> CheckoutDTO:
        # Stripe requires a server-side call to mint a Checkout Session; our
        # payment id rides in client_reference_id so the webhook binds back to it.
        form = {
            "mode": "payment",
            "client_reference_id": str(payment.id),
            "success_url": return_url or self._success_url,
            "cancel_url": self._cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": payment.amount.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(to_stripe_amount(payment.amount)),
            "line_items[0][price_data][product_data][name]": payment.purpose or "Payment",
            "metadata[payment_id]": str(payment.id),
        }

        async def _op() -> dict[str, Any]:
            return await self._post_checkout(form)

        session = await call_resilient(
            _op,
            timeout=_TIMEOUT,
            retry=RetryPolicy(attempts=3),
            breaker=self._breaker,
            transient=_CHECKOUT_TRANSIENT,
            error_cls=PaymentProviderError,
        )
        url = session.get("url")
        if not url:
            raise PaymentProviderError("Stripe checkout session has no url")
        return CheckoutDTO(
            payment_id=payment.id,
            provider=self.code,
            checkout_url=str(url),
            expires_at=None,  # our own TTL/expiry job governs abandonment (task 13)
        )

    async def _post_checkout(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT) as client:
                response = await client.post(
                    f"{self.api_base_url}{self.checkout_path}",
                    content=urlencode(form),
                    headers={
                        "Authorization": f"Bearer {self._secret_key}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
        except httpx.HTTPError as exc:
            raise _StripeTransientError(f"stripe transport error: {exc}") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise _StripeTransientError(f"stripe http {response.status_code}")
        if response.status_code >= 400:
            raise PaymentProviderError(f"stripe rejected checkout: http {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise PaymentProviderError("stripe returned a non-object checkout session")
        return data

    def parse_webhook(self, raw: RawWebhook) -> ProviderCallback:
        event = self._decode_event(raw.body)
        event_type = str(event.get("type"))
        obj = self._event_object(event)
        signature_valid = self._signature_ok(raw)

        action, reference, provider_txn_id, amount = self._normalize(event, event_type, obj)
        return ProviderCallback(
            provider=self.code,
            provider_txn_id=provider_txn_id,
            action=action,
            payment_reference=reference,
            amount=amount,
            raw={"id": event.get("id"), "type": event_type},
            signature_valid=signature_valid,
        )

    def build_webhook_response(self, outcome: CallbackOutcome) -> WebhookResponse:
        # Stripe only inspects the HTTP status: 2xx acknowledges (stop retrying),
        # non-2xx retries. A bad signature is not from Stripe (or is forged) -> 400.
        # Every decided outcome (ok/replay/not_found/amount_mismatch/invalid_state)
        # is a 200 ack; the forensic detail lives in the webhook ledger and logs.
        if outcome.status == "invalid_signature":
            return WebhookResponse(status_code=400, body={"error": "invalid signature"})
        return WebhookResponse(status_code=200, body={"received": True})

    # --- internals ---

    def _decode_event(self, body: str) -> dict[str, Any]:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError) as exc:
            raise WebhookVerificationError("stripe: body is not valid JSON") from exc
        if not isinstance(data, dict) or "type" not in data or "data" not in data:
            raise WebhookVerificationError("stripe: not an Event object")
        return data

    def _event_object(self, event: dict[str, Any]) -> dict[str, Any]:
        data = event.get("data")
        obj = data.get("object") if isinstance(data, dict) else None
        return obj if isinstance(obj, dict) else {}

    def _normalize(
        self, event: dict[str, Any], event_type: str, obj: dict[str, Any]
    ) -> tuple[CallbackAction, str, str | None, Money]:
        """Map a Stripe event onto (action, payment_reference, provider_txn_id,
        amount). The dedup ledger keys on the event id (Stripe redelivers keep
        it), while the payment is located by client_reference_id (our payment id).
        Unhandled event types become a benign read-only ``status`` no-op."""
        event_id = str(event["id"]) if event.get("id") is not None else None
        reference = str(obj.get("client_reference_id") or "")

        if event_type == EVENT_COMPLETED and obj.get("payment_status") == "paid":
            amount = minor_units_from_stripe(obj.get("amount_total"), obj.get("currency"))
            return "confirm", reference, event_id, amount
        if event_type == EVENT_EXPIRED:
            # Amount left at 0 -> reconciliation skipped: a cancel must not be
            # blocked by an amount check (mirrors the sibling adapters).
            return "cancel", reference, event_id, Money(0, _safe_currency(obj))
        # Not one we act on: acknowledge without touching state. Empty reference +
        # no txn id -> the processor's read-only path finds no payment and answers
        # 200 (Stripe ignores the body).
        return "status", "", None, Money(0, _safe_currency(obj))

    def _signature_ok(self, raw: RawWebhook) -> bool:
        header = _header(raw.headers, "stripe-signature")
        if not header:
            return False
        timestamp, signatures = _parse_signature_header(header)
        if timestamp is None or not signatures:
            return False
        signed_payload = f"{timestamp}.{raw.body}".encode()
        expected = hmac.new(
            self._webhook_secret.encode(), signed_payload, hashlib.sha256
        ).hexdigest()
        return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


def _safe_currency(obj: dict[str, Any]) -> str:
    """Currency for a zero-amount Money on non-confirm actions; falls back to USD
    when the event object carries none (the amount is 0, so it is never checked)."""
    code = str(obj.get("currency") or "").upper()
    return code if len(code) == 3 and code.isalpha() else "USD"


def _parse_signature_header(header: str) -> tuple[str | None, list[str]]:
    """Parse a Stripe-Signature header ``t=...,v1=...,v1=...`` into its timestamp
    and the list of v1 (HMAC-SHA256) signatures (multiple during key rotation)."""
    timestamp: str | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup over a plain mapping."""
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return str(value)
    return None
