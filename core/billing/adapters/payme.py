"""Payme (Paycom) Merchant API adapter (interfaces §4.1).

Payme drives the flow: after we hand the payer a checkout URL, Payme calls our
webhook with JSON-RPC 2.0 methods. Auth is HTTP Basic ``Paycom:<merchant_key>``
(compared in constant time). Amounts are in **tiyin** (1 sum = 100 tiyin); UZS
in our ledger has exponent 0 (minor unit = 1 sum), so the conversion factor is
100, NOT 1 — the classic integration bug this adapter guards with a dedicated
test.

The payment state machine lives in billing (state_machine.py), not here: this
adapter only verifies, normalizes inbound callbacks, and renders the JSON-RPC
answer for an outcome billing computed. Transaction times (create/perform/
cancel) are the ones billing recorded at the transition, so a duplicate call or a
CheckTransaction always gets the same values Payme saw first.
"""

import base64
import binascii
import hmac
import json
from datetime import datetime
from typing import Any, ClassVar

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

TIYIN_PER_SUM = 100  # Payme's minor unit is 1/100 of a sum
PAYME_CURRENCY = "UZS"

# JSON-RPC method -> normalized callback action.
_METHOD_ACTIONS: dict[str, CallbackAction] = {
    "CheckPerformTransaction": "check",
    "CreateTransaction": "create",
    "PerformTransaction": "confirm",
    "CancelTransaction": "cancel",
    "CheckTransaction": "status",
}
# Methods keyed by Payme's transaction id (no account, no amount).
_TXN_KEYED_ACTIONS = frozenset({"confirm", "cancel", "status"})

# Payme JSON-RPC error codes (subset used by this integration).
ERR_INSUFFICIENT_PRIVILEGE = -32504
ERR_INVALID_AMOUNT = -31001
ERR_TRANSACTION_NOT_FOUND = -31003
ERR_UNABLE_TO_PERFORM = -31008
ERR_ORDER_NOT_FOUND = -31050  # merchant-defined range (-31099..-31050)

_ERROR_MESSAGES: dict[str, tuple[int, str]] = {
    "invalid_signature": (ERR_INSUFFICIENT_PRIVILEGE, "Insufficient privilege"),
    "amount_mismatch": (ERR_INVALID_AMOUNT, "Invalid amount"),
    "invalid_state": (ERR_UNABLE_TO_PERFORM, "Unable to perform operation"),
    "not_found": (ERR_ORDER_NOT_FOUND, "Order not found"),
}

# Payme transaction states: 1 open, 2 performed, -1 canceled before perform.
STATE_OPEN = 1
STATE_PERFORMED = 2
STATE_CANCELED = -1
# Payme cancel reason for a transaction closed on timeout (our checkout expiry).
REASON_TIMEOUT = 4


def to_tiyin(money: Money) -> int:
    """Ledger minor units (UZS, exponent 0 => sums) -> Payme tiyin."""
    if money.currency != PAYME_CURRENCY:
        raise PaymentProviderError(f"Payme supports only {PAYME_CURRENCY}, got {money.currency}")
    return money.amount * TIYIN_PER_SUM


def minor_units_from_tiyin(tiyin: int) -> int:
    """Payme tiyin -> ledger minor units (sums). Rejects sub-sum amounts, which
    UZS cannot represent (they would silently truncate)."""
    if not isinstance(tiyin, int) or tiyin < 0 or tiyin % TIYIN_PER_SUM != 0:
        raise PaymentProviderError(f"invalid Payme amount in tiyin: {tiyin!r}")
    return tiyin // TIYIN_PER_SUM


def _ms(moment: datetime | None) -> int:
    return int(moment.timestamp() * 1000) if moment is not None else 0


def _state(payment: PaymentDTO) -> int:
    if payment.status == "succeeded":
        return STATE_PERFORMED
    if payment.status in ("created", "pending"):
        return STATE_OPEN
    return STATE_CANCELED  # canceled/failed/expired before perform (no refunds in v1)


def _reason(payment: PaymentDTO) -> int | None:
    if _state(payment) != STATE_CANCELED:
        return None
    if payment.failure_code is not None and payment.failure_code.lstrip("-").isdigit():
        return int(payment.failure_code)
    return REASON_TIMEOUT if payment.status == "expired" else None


class PaymeProvider:
    code: ClassVar[str] = "payme"
    checkout_base_url: ClassVar[str] = "https://checkout.paycom.uz"
    # Merchant "account" key carrying our payment id (configured in the Payme cabinet).
    account_field: ClassVar[str] = "payment_id"

    def __init__(self, *, merchant_id: str, merchant_key: str) -> None:
        if not merchant_id or not merchant_key:
            raise PaymentProviderError("Payme adapter requires merchant_id and merchant_key")
        self._merchant_id = merchant_id
        self._expected_auth = f"Paycom:{merchant_key}"

    @classmethod
    def from_settings(cls, settings: Settings) -> "PaymeProvider":
        return cls(merchant_id=settings.payme_merchant_id, merchant_key=settings.payme_merchant_key)

    async def create_checkout(self, payment: PaymentDTO, return_url: str | None) -> CheckoutDTO:
        # Payme checkout is a base64-encoded parameter string appended to the URL;
        # no server-to-server call, so nothing to retry here. return_url is
        # validated by PaymentService (no ';' that could inject a parameter).
        params = [
            f"m={self._merchant_id}",
            f"ac.{self.account_field}={payment.id}",
            f"a={to_tiyin(payment.amount)}",
        ]
        if return_url:
            params.append(f"c={return_url}")
        # Payme takes the raw standard-base64 params as the URL path (its own scheme).
        encoded = base64.b64encode(";".join(params).encode()).decode()
        return CheckoutDTO(
            payment_id=payment.id,
            provider=self.code,
            checkout_url=f"{self.checkout_base_url}/{encoded}",
            expires_at=None,  # our own TTL/expiry job governs abandonment
        )

    def parse_webhook(self, raw: RawWebhook) -> ProviderCallback:
        data = self._decode_jsonrpc(raw.body)
        method = data.get("method")
        action = _METHOD_ACTIONS.get(str(method)) if method is not None else None
        if action is None:
            # Recognizable JSON-RPC but a method we do not implement: no clean
            # dialect answer -> fall back to the generic verification error.
            raise WebhookVerificationError(f"payme: unsupported method {method!r}")
        params = data.get("params") or {}
        if not isinstance(params, dict):
            raise WebhookVerificationError("payme: params is not an object")

        provider_txn_id, reference, amount = self._normalize(action, params)
        reason = params.get("reason") if action == "cancel" else None
        return ProviderCallback(
            provider=self.code,
            provider_txn_id=provider_txn_id,
            action=action,
            payment_reference=reference,
            amount=amount,
            raw={"id": data.get("id"), "method": method, "params": params},
            signature_valid=self._auth_ok(raw),
            reason=str(reason) if reason is not None else None,
        )

    def build_webhook_response(self, outcome: CallbackOutcome) -> WebhookResponse:
        request_id = self._request_id(outcome)
        action = outcome.callback.action if outcome.callback is not None else None
        status = outcome.status
        if (
            action == "create"
            and status in ("ok", "already_processed")
            and outcome.payment is not None
            and outcome.payment.status != "pending"
        ):
            # A repeated CreateTransaction is answered only while it is still open.
            status = "invalid_state"
        if status in ("ok", "already_processed"):
            # A repeated delivery is answered deterministically from payment state
            # (same result as the first success) — Payme dedups on our transaction.
            return WebhookResponse(
                status_code=200,
                body={"result": self._success_result(outcome), "id": request_id},
            )
        code, message = _ERROR_MESSAGES.get(
            status, (ERR_UNABLE_TO_PERFORM, "Unable to perform operation")
        )
        if status == "not_found" and action in _TXN_KEYED_ACTIONS:
            code, message = ERR_TRANSACTION_NOT_FOUND, "Transaction not found"
        return WebhookResponse(
            status_code=200,
            body={"error": {"code": code, "message": message}, "id": request_id},
        )

    # --- internals ---

    def _decode_jsonrpc(self, body: str) -> dict[str, Any]:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError) as exc:
            raise WebhookVerificationError("payme: body is not valid JSON") from exc
        if not isinstance(data, dict) or "method" not in data:
            raise WebhookVerificationError("payme: not a JSON-RPC request")
        return data

    def _auth_ok(self, raw: RawWebhook) -> bool:
        header = raw.header("authorization")
        if not header or not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return False
        return hmac.compare_digest(decoded, self._expected_auth)

    def _normalize(
        self, action: CallbackAction, params: dict[str, Any]
    ) -> tuple[str | None, str, Money | None]:
        """Return (provider_txn_id, payment_reference, amount). For confirm/cancel/
        status Payme sends only its transaction id — the payment is located by it
        and carries no amount to reconcile (None)."""
        if action in ("check", "create"):
            account = params.get("account") or {}
            reference = (
                str(account.get(self.account_field, "")) if isinstance(account, dict) else ""
            )
            try:
                amount = Money(minor_units_from_tiyin(params.get("amount", 0)), PAYME_CURRENCY)
            except PaymentProviderError:
                amount = Money(0, PAYME_CURRENCY)  # unparseable: never reconciles
            provider_txn_id = str(params["id"]) if action == "create" and "id" in params else None
            return provider_txn_id, reference, amount
        provider_txn_id = str(params["id"]) if "id" in params else None
        return provider_txn_id, "", None

    def _request_id(self, outcome: CallbackOutcome) -> Any:
        if outcome.callback is not None:
            return outcome.callback.raw.get("id")
        return None

    def _success_result(self, outcome: CallbackOutcome) -> dict[str, Any]:
        """Provider-dialect success payload, rendered from the payment's recorded
        transaction times — identical for the first answer and every replay."""
        action = outcome.callback.action if outcome.callback is not None else None
        if action == "check":
            return {"allow": True}
        payment = outcome.payment
        if payment is None:  # pragma: no cover - success outcomes always carry a payment
            return {}
        transaction = str(payment.id)
        if action == "create":
            return {
                "create_time": _ms(payment.pending_at),
                "transaction": transaction,
                "state": _state(payment),
            }
        if action == "confirm":
            return {
                "transaction": transaction,
                "perform_time": _ms(payment.paid_at),
                "state": _state(payment),
            }
        if action == "cancel":
            return {
                "transaction": transaction,
                "cancel_time": _ms(payment.closed_at),
                "state": _state(payment),
            }
        return {
            "create_time": _ms(payment.pending_at),
            "perform_time": _ms(payment.paid_at),
            "cancel_time": _ms(payment.closed_at),
            "transaction": transaction,
            "state": _state(payment),
            "reason": _reason(payment),
        }
