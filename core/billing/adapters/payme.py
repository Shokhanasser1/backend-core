"""Payme (Paycom) Merchant API adapter (interfaces §4.1).

Payme drives the flow: after we hand the payer a checkout URL, Payme calls our
webhook with JSON-RPC 2.0 methods. Auth is HTTP Basic ``Paycom:<merchant_key>``
(compared in constant time). Amounts are in **tiyin** (1 sum = 100 tiyin); UZS
in our ledger has exponent 0 (minor unit = 1 sum), so the conversion factor is
100, NOT 1 — the classic integration bug this adapter guards with a dedicated
test.

The payment state machine lives in billing (state_machine.py), not here: this
adapter only verifies, normalizes inbound callbacks, and renders the JSON-RPC
answer for an outcome billing computed.
"""

import base64
import binascii
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
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

# Payme JSON-RPC error codes (subset used by this integration).
ERR_INSUFFICIENT_PRIVILEGE = -32504
ERR_INVALID_AMOUNT = -31001
ERR_UNABLE_TO_PERFORM = -31008
ERR_ORDER_NOT_FOUND = -31050  # merchant-defined range (-31099..-31050)

_ERROR_MESSAGES: dict[str, tuple[int, str]] = {
    "invalid_signature": (ERR_INSUFFICIENT_PRIVILEGE, "Insufficient privilege"),
    "amount_mismatch": (ERR_INVALID_AMOUNT, "Invalid amount"),
    "invalid_state": (ERR_UNABLE_TO_PERFORM, "Unable to perform operation"),
    "not_found": (ERR_ORDER_NOT_FOUND, "Order not found"),
}


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


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


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
        # no server-to-server call, so nothing to retry here.
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
            expires_at=None,  # our own TTL/expiry job governs abandonment (task 13)
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
        return ProviderCallback(
            provider=self.code,
            provider_txn_id=provider_txn_id,
            action=action,
            payment_reference=reference,
            amount=amount,
            raw={"id": data.get("id"), "method": method, "params": params},
            signature_valid=self._auth_ok(raw.headers),
        )

    def build_webhook_response(self, outcome: CallbackOutcome) -> WebhookResponse:
        request_id = self._request_id(outcome)
        if outcome.status in ("ok", "already_processed"):
            # A repeated delivery is answered deterministically from payment state
            # (same result as the first success) — Payme dedups on our transaction.
            return WebhookResponse(
                status_code=200,
                body={"result": self._success_result(outcome), "id": request_id},
            )
        code, message = _ERROR_MESSAGES.get(
            outcome.status, (ERR_UNABLE_TO_PERFORM, "Unable to perform operation")
        )
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

    def _auth_ok(self, headers: Mapping[str, str]) -> bool:
        header = _header(headers, "authorization")
        if not header or not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return False
        return hmac.compare_digest(decoded, self._expected_auth)

    def _normalize(
        self, action: CallbackAction, params: dict[str, Any]
    ) -> tuple[str | None, str, Money]:
        """Return (provider_txn_id, payment_reference, amount). For confirm/cancel/
        status Payme sends only its transaction id — reference/amount are looked up
        from the stored payment by the router (task 13), so they are left empty."""
        if action in ("check", "create"):
            account = params.get("account") or {}
            reference = (
                str(account.get(self.account_field, "")) if isinstance(account, dict) else ""
            )
            amount = Money(minor_units_from_tiyin(params.get("amount", 0)), PAYME_CURRENCY)
            provider_txn_id = str(params["id"]) if action == "create" and "id" in params else None
            return provider_txn_id, reference, amount
        # confirm | cancel | status: keyed by Payme's transaction id only.
        provider_txn_id = str(params["id"]) if "id" in params else None
        return provider_txn_id, "", Money(0, PAYME_CURRENCY)

    def _request_id(self, outcome: CallbackOutcome) -> Any:
        if outcome.callback is not None:
            return outcome.callback.raw.get("id")
        return None

    def _success_result(self, outcome: CallbackOutcome) -> dict[str, Any]:
        """Provider-dialect success payload. The transaction lifecycle fields
        (create_time/perform_time/state) are finalized in task 13 once Payme's
        per-transaction timestamps are persisted; here we render what the action
        determines unambiguously."""
        action = outcome.callback.action if outcome.callback is not None else None
        if action == "check":
            return {"allow": True}
        transaction = str(outcome.payment.id) if outcome.payment is not None else ""
        if action == "create":
            return {"create_time": _now_ms(), "transaction": transaction, "state": 1}
        if action == "confirm":
            return {"transaction": transaction, "perform_time": _now_ms(), "state": 2}
        if action == "cancel":
            return {"transaction": transaction, "cancel_time": _now_ms(), "state": -1}
        return {"transaction": transaction, "state": 2}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup over a plain mapping."""
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return str(value)
    return None
