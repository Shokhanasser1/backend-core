"""Click Merchant (SHOP-API) adapter (interfaces §4.1).

Click drives the flow with two form-encoded callbacks: Prepare (action=0) and
Complete (action=1). Authentication is an ``md5`` ``sign_string`` over the request
fields plus the secret key, compared in constant time. Amounts are in **sums**
(possibly with a decimal fraction); UZS in our ledger has exponent 0, so a Click
amount maps 1:1 to ledger minor units (the fractional part, if any, must be zero).
"""

import hashlib
import hmac
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar
from urllib.parse import urlencode

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

CLICK_CURRENCY = "UZS"

# Click action code -> normalized callback action.
ACTION_PREPARE = "0"
ACTION_COMPLETE = "1"
_ACTIONS: dict[str, CallbackAction] = {ACTION_PREPARE: "create", ACTION_COMPLETE: "confirm"}

# Click SHOP-API error codes (subset).
ERR_SUCCESS = 0
ERR_SIGN_CHECK_FAILED = -1
ERR_INCORRECT_AMOUNT = -2
ERR_ALREADY_PAID = -4
ERR_ORDER_NOT_FOUND = -5
ERR_TRANSACTION_NOT_FOUND = -6

_ERROR_CODES: dict[str, tuple[int, str]] = {
    "invalid_signature": (ERR_SIGN_CHECK_FAILED, "SIGN CHECK FAILED"),
    "amount_mismatch": (ERR_INCORRECT_AMOUNT, "Incorrect parameter amount"),
    "not_found": (ERR_ORDER_NOT_FOUND, "Order not found"),
    "invalid_state": (ERR_ALREADY_PAID, "Already paid"),
}


def to_sum_amount(money: Money) -> str:
    """Ledger minor units (UZS, exponent 0 => sums) -> Click amount string."""
    if money.currency != CLICK_CURRENCY:
        raise PaymentProviderError(f"Click supports only {CLICK_CURRENCY}, got {money.currency}")
    return str(money.amount)


def minor_units_from_sum(value: str) -> int:
    """Click amount (sums, maybe with a .00 fraction) -> ledger minor units.
    UZS has no sub-sum unit, so a non-integral amount is rejected."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PaymentProviderError(f"invalid Click amount: {value!r}") from exc
    if amount < 0 or amount != amount.to_integral_value():
        raise PaymentProviderError(f"non-integral Click amount for UZS: {value!r}")
    return int(amount)


class ClickProvider:
    code: ClassVar[str] = "click"
    checkout_base_url: ClassVar[str] = "https://my.click.uz/services/pay"

    def __init__(self, *, service_id: str, merchant_id: str, secret_key: str) -> None:
        if not service_id or not merchant_id or not secret_key:
            raise PaymentProviderError(
                "Click adapter requires service_id, merchant_id and secret_key"
            )
        self._service_id = service_id
        self._merchant_id = merchant_id
        self._secret_key = secret_key

    @classmethod
    def from_settings(cls, settings: Settings) -> "ClickProvider":
        return cls(
            service_id=settings.click_service_id,
            merchant_id=settings.click_merchant_id,
            secret_key=settings.click_secret_key,
        )

    async def create_checkout(self, payment: PaymentDTO, return_url: str | None) -> CheckoutDTO:
        query = {
            "service_id": self._service_id,
            "merchant_id": self._merchant_id,
            "amount": to_sum_amount(payment.amount),
            "transaction_param": str(payment.id),
        }
        if return_url:
            query["return_url"] = return_url
        return CheckoutDTO(
            payment_id=payment.id,
            provider=self.code,
            checkout_url=f"{self.checkout_base_url}?{urlencode(query)}",
            expires_at=None,
        )

    def parse_webhook(self, raw: RawWebhook) -> ProviderCallback:
        form = raw.form
        if not isinstance(form, dict) or "action" not in form or "click_trans_id" not in form:
            raise WebhookVerificationError("click: not a SHOP-API callback")
        action_code = str(form.get("action"))
        action = _ACTIONS.get(action_code)
        if action is None:
            raise WebhookVerificationError(f"click: unsupported action {action_code!r}")

        return ProviderCallback(
            provider=self.code,
            provider_txn_id=str(form.get("click_trans_id")),
            action=action,
            payment_reference=str(form.get("merchant_trans_id", "")),
            amount=Money(minor_units_from_sum(form.get("amount", "0")), CLICK_CURRENCY),
            raw=dict(form),
            signature_valid=self._sign_ok(form, action_code),
        )

    def build_webhook_response(self, outcome: CallbackOutcome) -> WebhookResponse:
        raw = outcome.callback.raw if outcome.callback is not None else {}
        body: dict[str, Any] = {
            "click_trans_id": raw.get("click_trans_id"),
            "merchant_trans_id": raw.get("merchant_trans_id"),
        }
        # On Prepare we must return a merchant_prepare_id; echo the one from the
        # request on Complete. We use our payment id (stable, unique per payment).
        prepare_id = raw.get("merchant_prepare_id") or (
            str(outcome.payment.id) if outcome.payment is not None else None
        )
        if prepare_id is not None:
            body["merchant_prepare_id"] = prepare_id

        if outcome.status in ("ok", "already_processed"):
            body["error"] = ERR_SUCCESS
            body["error_note"] = "Success"
        else:
            code, note = _ERROR_CODES.get(
                outcome.status, (ERR_TRANSACTION_NOT_FOUND, "Transaction does not exist")
            )
            body["error"] = code
            body["error_note"] = note
        return WebhookResponse(status_code=200, body=body)

    # --- internals ---

    def _sign_ok(self, form: dict[str, Any], action_code: str) -> bool:
        provided = str(form.get("sign_string", ""))
        expected = self._sign(form, action_code)
        return hmac.compare_digest(provided, expected)

    def _sign(self, form: dict[str, Any], action_code: str) -> str:
        parts = [
            str(form.get("click_trans_id", "")),
            str(form.get("service_id", "")),
            self._secret_key,
            str(form.get("merchant_trans_id", "")),
        ]
        if action_code == ACTION_COMPLETE:
            parts.append(str(form.get("merchant_prepare_id", "")))
        parts.extend(
            [
                str(form.get("amount", "")),
                action_code,
                str(form.get("sign_time", "")),
            ]
        )
        # md5 is mandated by the Click SHOP-API signature scheme; not our choice.
        return hashlib.md5("".join(parts).encode()).hexdigest()  # noqa: S324
