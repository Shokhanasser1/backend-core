"""Payment-webhook hardening (audit batch 2): amount parsing never escapes the
provider dialect, a zero/garbage amount never passes reconciliation, Payme's
transaction lifecycle fields are real and stable, Click reports failed payments,
Stripe enforces a replay window, and rejected traffic is journalled bounded."""

import hashlib
import hmac
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from core.billing.adapters.click import ClickProvider
from core.billing.adapters.payme import PaymeProvider
from core.billing.adapters.stripe import StripeProvider
from core.billing.ports import CallbackOutcome, RawWebhook
from core.billing.schemas import PaymentDTO
from core.billing.webhooks import WebhookProcessor
from shared.errors import PaymentProviderError
from shared.money import Money
from tests.test_billing_webhooks import (
    CLICK_SECRET,
    PAYME_ERR_INVALID_AMOUNT,
    PAYME_ERR_UNABLE_TO_PERFORM,
    PAYME_KEY,
    STRIPE_WEBHOOK_SECRET,
    _click_form,
    _click_raw,
    _err_code,
    _fetch,
    _payme_raw,
    _payment_status,
    _processor,
    _seed,
    _subscription_status,
)

PAYME_ERR_TXN_NOT_FOUND = -31003
CLICK_ERR_CANCELLED = -9


# ------------------------------------------------------------- adapters (unit)


def test_raw_webhook_header_lookup_is_case_insensitive() -> None:
    raw = RawWebhook(headers={"Stripe-Signature": "t=1"}, body="")
    assert raw.header("stripe-signature") == "t=1"
    assert raw.header("missing") is None


@pytest.mark.parametrize("key", [PAYME_KEY, "wrong"])
def test_payme_garbage_amount_is_normalized_not_raised(key: str) -> None:
    provider = PaymeProvider(merchant_id="m", merchant_key=PAYME_KEY)
    raw = _payme_raw(
        "CreateTransaction",
        {"id": "t1", "account": {"payment_id": str(uuid4())}, "amount": "lots"},
        key=key,
    )

    callback = provider.parse_webhook(raw)

    assert callback.amount == Money(0, "UZS")  # never reconciles against a real payment
    assert callback.signature_valid is (key == PAYME_KEY)


@pytest.mark.parametrize("method", ["PerformTransaction", "CancelTransaction", "CheckTransaction"])
def test_payme_txn_keyed_methods_carry_no_amount(method: str) -> None:
    provider = PaymeProvider(merchant_id="m", merchant_key=PAYME_KEY)
    callback = provider.parse_webhook(_payme_raw(method, {"id": "t1", "reason": 5}))
    assert callback.amount is None


def test_click_garbage_amount_is_normalized_not_raised() -> None:
    provider = ClickProvider(service_id="svc-1", merchant_id="merch-1", secret_key=CLICK_SECRET)
    form = _click_form(action="0", click_trans_id="1", merchant_trans_id="x", amount="12.5")
    callback = provider.parse_webhook(_click_raw(form))
    assert callback.amount == Money(0, "UZS")
    assert callback.signature_valid is True


def test_click_complete_with_error_is_a_cancel() -> None:
    provider = ClickProvider(service_id="svc-1", merchant_id="merch-1", secret_key=CLICK_SECRET)
    form = _click_form(
        action="1", click_trans_id="9", merchant_trans_id="x", amount="5000", prepare_id="p"
    )
    form["error"] = "-5017"  # Click: payment failed on its side (e.g. insufficient funds)

    callback = provider.parse_webhook(_click_raw(form))

    assert callback.action == "cancel"
    assert callback.reason == "-5017"
    assert callback.amount is None
    response = provider.build_webhook_response(CallbackOutcome(status="ok", callback=callback))
    assert response.body["error"] == CLICK_ERR_CANCELLED


def _stripe(now: float) -> StripeProvider:
    return StripeProvider(
        secret_key="sk_test_x",
        webhook_secret=STRIPE_WEBHOOK_SECRET,
        success_url="https://shop.example/ok",
        cancel_url="https://shop.example/cancel",
        clock=lambda: now,
    )


def _stripe_signed(timestamp: int) -> RawWebhook:
    body = json.dumps({"id": "evt_t", "type": "payment_intent.created", "data": {"object": {}}})
    v1 = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return RawWebhook(headers={"Stripe-Signature": f"t={timestamp},v1={v1}"}, body=body)


@pytest.mark.parametrize(("age", "valid"), [(0, True), (299, True), (301, False), (-301, False)])
def test_stripe_signature_has_a_replay_window(age: int, valid: bool) -> None:
    now = 1_800_000_000
    callback = _stripe(float(now)).parse_webhook(_stripe_signed(now - age))
    assert callback.signature_valid is valid


async def test_stripe_checkout_is_a_single_bounded_call() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    provider = StripeProvider(
        secret_key="sk_test_x",
        webhook_secret=STRIPE_WEBHOOK_SECRET,
        success_url="https://shop.example/ok",
        cancel_url="https://shop.example/cancel",
        transport=httpx.MockTransport(handler),
    )
    payment = PaymentDTO(
        id=uuid4(),
        status="created",
        amount=Money(5000, "USD"),
        purpose="topup",
        reference="r",
        provider="stripe",
        paid_at=None,
    )
    with pytest.raises(PaymentProviderError):
        await provider.create_checkout(payment, None)
    # Runs inside the request's DB transaction: no in-request retries (the
    # client's idempotency key covers a retry).
    assert calls == [1]


# ------------------------------------------------------- processor (integration)


@pytest.mark.parametrize("amount", [0, "abc"])
async def test_payme_zero_or_garbage_amount_is_a_mismatch(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
    amount: object,
) -> None:
    seed = await _seed(role_urls, payment_status="created")
    proc = _processor(maintenance_session_factory, test_settings)
    params = {"id": "tz", "account": {"payment_id": str(seed.payment_id)}, "amount": amount}

    for method in ("CheckPerformTransaction", "CreateTransaction"):
        _, body = await proc.process("payme", _payme_raw(method, params))
        assert _err_code(body) == PAYME_ERR_INVALID_AMOUNT
    assert await _payment_status(role_urls, seed.payment_id) == "created"


async def _result(proc: WebhookProcessor, method: str, params: dict[str, object]) -> dict[str, Any]:
    """Process one Payme call and return its JSON-RPC ``result`` (must succeed)."""
    _, body = await proc.process("payme", _payme_raw(method, params))
    result = body.get("result")
    assert isinstance(result, dict), body
    return result


async def test_payme_transaction_lifecycle_fields_are_real_and_stable(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="created")
    proc = _processor(maintenance_session_factory, test_settings)
    create = {
        "id": "life-1",
        "account": {"payment_id": str(seed.payment_id)},
        "amount": seed.amount * 100,
    }

    created = await _result(proc, "CreateTransaction", create)
    replay = await _result(proc, "CreateTransaction", create)
    check_open = await _result(proc, "CheckTransaction", {"id": "life-1"})

    create_time = created["create_time"]
    assert created["state"] == 1
    assert replay["create_time"] == create_time
    assert check_open == {
        "create_time": create_time,
        "perform_time": 0,
        "cancel_time": 0,
        "transaction": str(seed.payment_id),
        "state": 1,
        "reason": None,
    }

    performed = await _result(proc, "PerformTransaction", {"id": "life-1"})
    check_done = await _result(proc, "CheckTransaction", {"id": "life-1"})
    assert performed["state"] == 2
    assert performed["perform_time"] >= create_time
    assert check_done["state"] == 2
    assert check_done["perform_time"] == performed["perform_time"]

    # A CreateTransaction replay once the transaction is no longer open is refused.
    _, late = await proc.process("payme", _payme_raw("CreateTransaction", create, rpc_id=3))
    assert _err_code(late) == PAYME_ERR_UNABLE_TO_PERFORM


async def test_payme_cancel_reports_state_reason_and_time(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="pending", provider_txn_id="cx-1")
    proc = _processor(maintenance_session_factory, test_settings)

    canceled = await _result(proc, "CancelTransaction", {"id": "cx-1", "reason": 5})
    checked = await _result(proc, "CheckTransaction", {"id": "cx-1"})

    assert canceled["state"] == -1
    assert checked["state"] == -1
    assert checked["reason"] == 5
    assert checked["cancel_time"] == canceled["cancel_time"]
    assert await _payment_status(role_urls, seed.payment_id) == "canceled"
    assert await _subscription_status(role_urls, seed.subscription_id) == "canceled"


@pytest.mark.parametrize("method", ["PerformTransaction", "CancelTransaction", "CheckTransaction"])
async def test_payme_unknown_transaction_is_txn_not_found(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    method: str,
) -> None:
    proc = _processor(maintenance_session_factory, test_settings)
    _, body = await proc.process("payme", _payme_raw(method, {"id": "ghost", "reason": 1}))
    assert _err_code(body) == PAYME_ERR_TXN_NOT_FOUND


async def test_click_failed_complete_cancels_payment(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="pending", provider="click", provider_txn_id="c9")
    proc = _processor(maintenance_session_factory, test_settings)
    form = _click_form(
        action="1",
        click_trans_id="c9",
        merchant_trans_id=str(seed.payment_id),
        amount=str(seed.amount),
        prepare_id=str(seed.payment_id),
    )
    form["error"] = "-5017"

    _, body = await proc.process("click", _click_raw(form))

    assert body["error"] == CLICK_ERR_CANCELLED
    assert await _payment_status(role_urls, seed.payment_id) == "canceled"
    assert await _subscription_status(role_urls, seed.subscription_id) == "canceled"


async def test_rejected_journal_body_is_bounded(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    proc = _processor(maintenance_session_factory, test_settings)
    params = {"id": "big", "account": {"payment_id": "x" * 50_000}, "amount": 100}

    await proc.process("payme", _payme_raw("CreateTransaction", params, key="wrong"))

    rows = await _fetch(role_urls, "SELECT length(raw_body) AS n FROM payment_webhooks", {})
    assert len(rows) == 1
    assert rows[0]["n"] <= 4096  # type: ignore[operator]
