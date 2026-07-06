"""Unit tests for the Payme and Click adapters (interfaces §4.1).

Pure functions over in-memory requests: amount-unit conversion (the mandatory
tiyin/sum guard), checkout-URL building, signature/auth verification, callback
normalization and the provider-dialect responses. No DB, no Docker."""

import base64
import hashlib
import json
from uuid import uuid4

import pytest

from app.config import Settings
from core.billing.adapters import build_payment_providers
from core.billing.adapters.click import (
    ClickProvider,
    minor_units_from_sum,
    to_sum_amount,
)
from core.billing.adapters.payme import (
    PaymeProvider,
    minor_units_from_tiyin,
    to_tiyin,
)
from core.billing.ports import CallbackOutcome, ProviderCallback, RawWebhook
from core.billing.schemas import PaymentDTO
from shared.errors import (
    InvariantViolationError,
    PaymentProviderError,
    WebhookVerificationError,
)
from shared.money import Money

PAYME_KEY = "secret-merchant-key"
CLICK_SECRET = "click-secret"


def _payment(amount: int = 5000) -> PaymentDTO:
    return PaymentDTO(
        id=uuid4(),
        status="created",
        amount=Money(amount, "UZS"),
        purpose="subscription",
        reference="ref-1",
        provider="payme",
        paid_at=None,
    )


def _payme() -> PaymeProvider:
    return PaymeProvider(merchant_id="merchant-1", merchant_key=PAYME_KEY)


def _payme_auth(key: str = PAYME_KEY) -> str:
    return "Basic " + base64.b64encode(f"Paycom:{key}".encode()).decode()


def _click() -> ClickProvider:
    return ClickProvider(service_id="svc-1", merchant_id="merch-1", secret_key=CLICK_SECRET)


def _click_prepare_form(payment_id: str, amount: str = "5000") -> dict[str, str]:
    fields = {
        "click_trans_id": "111",
        "service_id": "svc-1",
        "merchant_trans_id": payment_id,
        "amount": amount,
        "action": "0",
        "sign_time": "2026-07-07 10:00:00",
    }
    # Independently recompute the md5 sign the adapter must accept (Prepare has no
    # merchant_prepare_id in the signed string).
    signed = (
        f"{fields['click_trans_id']}{fields['service_id']}{CLICK_SECRET}"
        f"{fields['merchant_trans_id']}{fields['amount']}{fields['action']}{fields['sign_time']}"
    )
    fields["sign_string"] = hashlib.md5(signed.encode()).hexdigest()  # noqa: S324 - Click scheme
    return fields


# --- mandatory amount-unit conversion tests ---


def test_webhook_amount_unit_conversion_payme() -> None:
    # UZS exponent 0 -> the tiyin factor is 100, not 1 (the classic bug).
    assert to_tiyin(Money(5000, "UZS")) == 500_000
    assert minor_units_from_tiyin(500_000) == 5000
    # Round-trips for a range of values.
    for sums in (1, 99, 12_345):
        assert minor_units_from_tiyin(to_tiyin(Money(sums, "UZS"))) == sums
    # Sub-sum tiyin amounts cannot be represented in UZS -> rejected, not truncated.
    with pytest.raises(PaymentProviderError):
        minor_units_from_tiyin(150)
    with pytest.raises(PaymentProviderError):
        to_tiyin(Money(100, "USD"))


def test_webhook_amount_unit_conversion_click() -> None:
    assert to_sum_amount(Money(5000, "UZS")) == "5000"
    assert minor_units_from_sum("5000") == 5000
    assert minor_units_from_sum("5000.00") == 5000  # trailing .00 fraction is fine
    for sums in (1, 99, 12_345):
        assert minor_units_from_sum(to_sum_amount(Money(sums, "UZS"))) == sums
    # A real sub-sum fraction has no UZS representation -> rejected.
    with pytest.raises(PaymentProviderError):
        minor_units_from_sum("5000.50")
    with pytest.raises(PaymentProviderError):
        to_sum_amount(Money(100, "USD"))


# --- Payme adapter ---


async def test_payme_create_checkout_encodes_params() -> None:
    payment = _payment(amount=7000)
    checkout = await _payme().create_checkout(payment, return_url="https://shop.uz/back")
    assert checkout.checkout_url.startswith("https://checkout.paycom.uz/")
    encoded = checkout.checkout_url.removeprefix("https://checkout.paycom.uz/")
    decoded = base64.b64decode(encoded).decode()
    assert "m=merchant-1" in decoded
    assert f"ac.payment_id={payment.id}" in decoded
    assert "a=700000" in decoded  # 7000 sums -> 700000 tiyin
    assert "c=https://shop.uz/back" in decoded


def test_payme_parse_valid_check_callback() -> None:
    payment = _payment()
    body = json.dumps(
        {
            "method": "CheckPerformTransaction",
            "params": {"amount": 500_000, "account": {"payment_id": str(payment.id)}},
            "id": 42,
        }
    )
    callback = _payme().parse_webhook(
        RawWebhook(headers={"Authorization": _payme_auth()}, body=body)
    )
    assert callback.action == "check"
    assert callback.signature_valid is True
    assert callback.provider_txn_id is None  # no Payme txn yet at check time
    assert callback.payment_reference == str(payment.id)
    assert callback.amount == Money(5000, "UZS")
    assert callback.raw["id"] == 42


def test_payme_parse_bad_auth_sets_signature_invalid() -> None:
    body = json.dumps(
        {"method": "CreateTransaction", "params": {"id": "pt-1", "amount": 500_000}, "id": 1}
    )
    callback = _payme().parse_webhook(
        RawWebhook(headers={"Authorization": _payme_auth("wrong-key")}, body=body)
    )
    assert callback.signature_valid is False
    assert callback.action == "create"
    assert callback.provider_txn_id == "pt-1"


def test_payme_parse_unrecognizable_body_raises() -> None:
    with pytest.raises(WebhookVerificationError):
        _payme().parse_webhook(RawWebhook(headers={}, body="not json"))
    with pytest.raises(WebhookVerificationError):
        _payme().parse_webhook(RawWebhook(headers={}, body=json.dumps({"foo": "bar"})))


def test_payme_parse_unsupported_method_raises() -> None:
    body = json.dumps({"method": "GetStatement", "params": {}, "id": 1})
    with pytest.raises(WebhookVerificationError):
        _payme().parse_webhook(RawWebhook(headers={"Authorization": _payme_auth()}, body=body))


def test_payme_response_invalid_signature_dialect() -> None:
    callback = ProviderCallback(
        provider="payme",
        provider_txn_id=None,
        action="check",
        payment_reference="r",
        amount=Money(5000, "UZS"),
        raw={"id": 7, "method": "CheckPerformTransaction", "params": {}},
    )
    response = _payme().build_webhook_response(
        CallbackOutcome(status="invalid_signature", callback=callback)
    )
    assert response.status_code == 200
    assert response.body["error"]["code"] == -32504
    assert response.body["id"] == 7


def test_payme_response_ok_check_allows() -> None:
    callback = ProviderCallback(
        provider="payme",
        provider_txn_id=None,
        action="check",
        payment_reference="r",
        amount=Money(5000, "UZS"),
        raw={"id": 9, "method": "CheckPerformTransaction", "params": {}},
    )
    response = _payme().build_webhook_response(CallbackOutcome(status="ok", callback=callback))
    assert response.body["result"] == {"allow": True}
    assert response.body["id"] == 9


def test_payme_response_not_found_dialect() -> None:
    response = _payme().build_webhook_response(CallbackOutcome(status="not_found"))
    assert response.body["error"]["code"] == -31050


# --- Click adapter ---


async def test_click_create_checkout_builds_query() -> None:
    payment = _payment(amount=5000)
    payment = payment.model_copy(update={"provider": "click"})
    checkout = await _click().create_checkout(payment, return_url="https://shop.uz/back")
    assert checkout.checkout_url.startswith("https://my.click.uz/services/pay?")
    assert "service_id=svc-1" in checkout.checkout_url
    assert "merchant_id=merch-1" in checkout.checkout_url
    assert "amount=5000" in checkout.checkout_url
    assert f"transaction_param={payment.id}" in checkout.checkout_url


def test_click_parse_valid_prepare_callback() -> None:
    payment_id = str(uuid4())
    form = _click_prepare_form(payment_id)
    callback = _click().parse_webhook(RawWebhook(headers={}, body="", form=form))
    assert callback.action == "create"
    assert callback.signature_valid is True
    assert callback.provider_txn_id == "111"
    assert callback.payment_reference == payment_id
    assert callback.amount == Money(5000, "UZS")


def test_click_parse_bad_sign_sets_signature_invalid() -> None:
    form = _click_prepare_form(str(uuid4()))
    form["sign_string"] = "deadbeef"  # tampered
    callback = _click().parse_webhook(RawWebhook(headers={}, body="", form=form))
    assert callback.signature_valid is False


def test_click_parse_non_callback_raises() -> None:
    with pytest.raises(WebhookVerificationError):
        _click().parse_webhook(RawWebhook(headers={}, body="", form={"foo": "bar"}))


def test_click_response_success_and_errors() -> None:
    form = _click_prepare_form(str(uuid4()))
    callback = _click().parse_webhook(RawWebhook(headers={}, body="", form=form))

    ok = _click().build_webhook_response(
        CallbackOutcome(status="ok", payment=_payment(), callback=callback)
    )
    assert ok.body["error"] == 0
    assert ok.body["click_trans_id"] == "111"
    assert ok.body["merchant_trans_id"] == callback.payment_reference

    bad_sign = _click().build_webhook_response(
        CallbackOutcome(status="invalid_signature", callback=callback)
    )
    assert bad_sign.body["error"] == -1

    mismatch = _click().build_webhook_response(
        CallbackOutcome(status="amount_mismatch", callback=callback)
    )
    assert mismatch.body["error"] == -2


# --- provider registry ---


def _settings(**overrides: str) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_registry_builds_enabled_providers() -> None:
    settings = _settings(
        enabled_payment_providers="payme,click",
        payme_merchant_id="m",
        payme_merchant_key="k",
        click_service_id="s",
        click_merchant_id="m",
        click_secret_key="sk",
    )
    providers = build_payment_providers(settings)
    assert set(providers) == {"payme", "click"}
    assert isinstance(providers["payme"], PaymeProvider)
    assert isinstance(providers["click"], ClickProvider)


def test_registry_empty_when_none_enabled() -> None:
    assert build_payment_providers(_settings()) == {}


def test_registry_missing_credentials_fails_loudly() -> None:
    # Enabled but no merchant credentials -> misconfiguration, fail at startup.
    with pytest.raises(PaymentProviderError):
        build_payment_providers(_settings(enabled_payment_providers="payme"))


def test_registry_unknown_provider_rejected() -> None:
    with pytest.raises(InvariantViolationError):
        build_payment_providers(_settings(enabled_payment_providers="stripe"))
