"""WebhookProcessor integration tests (interfaces §4.1, threat model V4).

The security-critical intake path, against a real Postgres with the full DB-role
separation (webhooks run as app_maintenance). Covers the mandatory V4 negatives:
invalid signature, replay, concurrent single-effect, cross-tenant binding,
amount mismatch (incl. the tiyin x100 unit bug), and succeeded-is-terminal.

Payments are seeded directly as the table owner (bypassing RLS) so each test
starts from an exact payment state; the processor then drives the transition and
we assert both the provider-dialect answer and the committed DB effect.
"""

import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.main import create_app
from core.billing.adapters.click import ClickProvider
from core.billing.adapters.payme import PaymeProvider
from core.billing.ports import RawWebhook
from core.billing.webhooks import WebhookProcessor
from shared.db_provisioning import ROLE_MAINTENANCE, ROLE_MIGRATOR, ROLE_USER
from shared.events import EventBus

pytestmark = pytest.mark.integration

PAYME_KEY = "test-merchant-key"
CLICK_SECRET = "click-secret"
PRO_PRICE = 50_000  # minor units, UZS (exponent 0 -> sums)

# Payme JSON-RPC error codes asserted below (mirror the adapter's mapping).
PAYME_ERR_INSUFFICIENT_PRIVILEGE = -32504
PAYME_ERR_INVALID_AMOUNT = -31001
PAYME_ERR_UNABLE_TO_PERFORM = -31008
PAYME_ERR_ORDER_NOT_FOUND = -31050


# --------------------------------------------------------------------------- seed


async def _seed(
    role_urls: dict[str, str],
    *,
    payment_status: str,
    provider: str = "payme",
    amount: int = PRO_PRICE,
    provider_txn_id: str | None = None,
    with_subscription: bool = True,
) -> SimpleNamespace:
    """Seed reference + a user + tenant + (optional) pending subscription + one
    payment in the requested state, as the table owner (RLS bypassed)."""
    tenant_id, user_id, payment_id = uuid4(), uuid4(), uuid4()
    subscription_id = uuid4() if with_subscription else None
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO currencies (code, exponent, name) VALUES ('UZS', 0, 'Som') "
                    "ON CONFLICT (code) DO NOTHING"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO plans (id, code, name, price_amount) "
                    "VALUES (:id, 'pro', CAST(:name AS jsonb), :price) "
                    "ON CONFLICT (code) DO NOTHING"
                ),
                {"id": uuid4(), "name": '{"ru": "Pro", "uz": "Pro"}', "price": amount},
            )
            await conn.execute(
                text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
                {"id": user_id, "email": f"{user_id}@example.uz"},
            )
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, owner_user_id) "
                    "VALUES (:id, 'Test', :slug, :owner)"
                ),
                {"id": tenant_id, "slug": str(tenant_id), "owner": user_id},
            )
            reference = str(payment_id)
            if with_subscription:
                plan_id = (
                    await conn.execute(text("SELECT id FROM plans WHERE code = 'pro'"))
                ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO subscriptions (id, tenant_id, plan_id, status, price_amount, "
                        "currency, current_period_start, current_period_end) VALUES "
                        "(:id, :t, :pl, 'pending', :p, 'UZS', now(), now() + interval '30 days')"
                    ),
                    {"id": subscription_id, "t": tenant_id, "pl": plan_id, "p": amount},
                )
                reference = str(subscription_id)
            await conn.execute(
                text(
                    "INSERT INTO payments (id, tenant_id, subscription_id, purpose, reference, "
                    "amount, currency, status, provider, provider_transaction_id, idempotency_key) "
                    "VALUES (:id, :t, :sub, :purpose, :ref, :amt, 'UZS', :st, :prov, :ptx, :ik)"
                ),
                {
                    "id": payment_id,
                    "t": tenant_id,
                    "sub": subscription_id,
                    "purpose": "subscription" if with_subscription else "topup",
                    "ref": reference,
                    "amt": amount,
                    "st": payment_status,
                    "prov": provider,
                    "ptx": provider_txn_id,
                    "ik": str(uuid4()),
                },
            )
    finally:
        await engine.dispose()
    return SimpleNamespace(
        tenant_id=tenant_id,
        payment_id=payment_id,
        subscription_id=subscription_id,
        amount=amount,
    )


async def _fetch(
    role_urls: dict[str, str], sql: str, params: dict[str, object]
) -> list[dict[str, object]]:
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).mappings().all()
            return [dict(row) for row in rows]
    finally:
        await engine.dispose()


def _err_code(body: dict[str, object]) -> int:
    """Extract the provider error code (Payme JSON-RPC error.code) for assertions."""
    error = body["error"]
    assert isinstance(error, dict)
    return int(error["code"])


async def _payment_status(role_urls: dict[str, str], payment_id: object) -> str:
    rows = await _fetch(role_urls, "SELECT status FROM payments WHERE id = :id", {"id": payment_id})
    return str(rows[0]["status"])


async def _subscription_status(role_urls: dict[str, str], subscription_id: object) -> str:
    rows = await _fetch(
        role_urls, "SELECT status FROM subscriptions WHERE id = :id", {"id": subscription_id}
    )
    return str(rows[0]["status"])


# ---------------------------------------------------------------------- builders


def _processor(sessions: async_sessionmaker[AsyncSession], settings: Settings) -> WebhookProcessor:
    return WebhookProcessor(
        maintenance_sessions=sessions,
        bus=EventBus(),  # no reliable subscribers registered -> no arq enqueue needed
        providers={
            "payme": PaymeProvider(merchant_id="merchant-1", merchant_key=PAYME_KEY),
            "click": ClickProvider(
                service_id="svc-1", merchant_id="merch-1", secret_key=CLICK_SECRET
            ),
        },
        settings=settings,
    )


def _payme_auth(key: str = PAYME_KEY) -> str:
    return "Basic " + base64.b64encode(f"Paycom:{key}".encode()).decode()


def _payme_raw(
    method: str, params: dict[str, object], *, rpc_id: int = 1, key: str = PAYME_KEY
) -> RawWebhook:
    body = json.dumps({"method": method, "params": params, "id": rpc_id})
    return RawWebhook(
        headers={"Authorization": _payme_auth(key), "Content-Type": "application/json"}, body=body
    )


def _click_form(
    *, action: str, click_trans_id: str, merchant_trans_id: str, amount: str, prepare_id: str = ""
) -> dict[str, str]:
    fields = {
        "click_trans_id": click_trans_id,
        "service_id": "svc-1",
        "merchant_trans_id": merchant_trans_id,
        "amount": amount,
        "action": action,
        "sign_time": "2026-07-07 10:00:00",
    }
    parts = [click_trans_id, "svc-1", CLICK_SECRET, merchant_trans_id]
    if action == "1":  # complete signs the merchant_prepare_id too
        fields["merchant_prepare_id"] = prepare_id
        parts.append(prepare_id)
    parts += [amount, action, fields["sign_time"]]
    fields["sign_string"] = hashlib.md5("".join(parts).encode()).hexdigest()  # noqa: S324
    return fields


def _click_raw(form: dict[str, str]) -> RawWebhook:
    return RawWebhook(
        headers={"Content-Type": "application/x-www-form-urlencoded"}, body="", form=form
    )


# ------------------------------------------------------------------ happy paths


async def test_payme_create_marks_pending(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="created")
    proc = _processor(maintenance_session_factory, test_settings)
    raw = _payme_raw(
        "CreateTransaction",
        {
            "id": "payme-txn-1",
            "account": {"payment_id": str(seed.payment_id)},
            "amount": seed.amount * 100,
        },
    )

    status, body = await proc.process("payme", raw)

    assert status == 200
    assert "result" in body  # JSON-RPC success dialect
    assert await _payment_status(role_urls, seed.payment_id) == "pending"
    rows = await _fetch(
        role_urls,
        "SELECT provider_transaction_id FROM payments WHERE id = :id",
        {"id": seed.payment_id},
    )
    assert rows[0]["provider_transaction_id"] == "payme-txn-1"


async def test_payme_confirm_succeeds_and_activates_subscription(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="pending", provider_txn_id="payme-txn-2")
    proc = _processor(maintenance_session_factory, test_settings)

    status, body = await proc.process(
        "payme", _payme_raw("PerformTransaction", {"id": "payme-txn-2"})
    )

    assert status == 200
    assert "result" in body
    assert await _payment_status(role_urls, seed.payment_id) == "succeeded"
    # Subscription activated in the SAME transaction as the payment success.
    assert await _subscription_status(role_urls, seed.subscription_id) == "active"


async def test_click_prepare_then_complete(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="created", provider="click")
    proc = _processor(maintenance_session_factory, test_settings)

    prepare = _click_raw(
        _click_form(
            action="0",
            click_trans_id="cl-1",
            merchant_trans_id=str(seed.payment_id),
            amount=str(seed.amount),
        )
    )
    _s1, b1 = await proc.process("click", prepare)
    assert b1["error"] == 0
    assert await _payment_status(role_urls, seed.payment_id) == "pending"

    complete = _click_raw(
        _click_form(
            action="1",
            click_trans_id="cl-1",
            merchant_trans_id=str(seed.payment_id),
            amount=str(seed.amount),
            prepare_id=str(seed.payment_id),
        )
    )
    _s2, b2 = await proc.process("click", complete)
    assert b2["error"] == 0
    assert await _payment_status(role_urls, seed.payment_id) == "succeeded"
    assert await _subscription_status(role_urls, seed.subscription_id) == "active"


# --------------------------------------------------------------- V4 negatives


async def test_invalid_signature_no_effect_and_no_poisoning(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="created")
    proc = _processor(maintenance_session_factory, test_settings)
    params = {
        "id": "txn-x",
        "account": {"payment_id": str(seed.payment_id)},
        "amount": seed.amount * 100,
    }

    bad = _payme_raw("CreateTransaction", params, key="wrong")
    _status, body = await proc.process("payme", bad)

    assert _err_code(body) == PAYME_ERR_INSUFFICIENT_PRIVILEGE
    assert await _payment_status(role_urls, seed.payment_id) == "created"  # untouched
    # Journalled for forensics, but under the rejected: namespace — not the legit key.
    wh = await _fetch(role_urls, "SELECT dedup_key, status FROM payment_webhooks", {})
    assert len(wh) == 1
    assert wh[0]["status"] == "rejected"
    assert str(wh[0]["dedup_key"]).startswith("rejected:")

    # The real callback with the SAME txn id still works: no poisoning of txn-x:create.
    _, good_body = await proc.process("payme", _payme_raw("CreateTransaction", params))
    assert "result" in good_body
    assert await _payment_status(role_urls, seed.payment_id) == "pending"


async def test_replay_is_single_effect(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="pending", provider_txn_id="txn-r")
    proc = _processor(maintenance_session_factory, test_settings)
    raw = _payme_raw("PerformTransaction", {"id": "txn-r"})

    _, first = await proc.process("payme", raw)
    _, second = await proc.process("payme", raw)  # duplicate delivery

    assert "result" in first
    assert "result" in second  # answered deterministically from committed state
    assert await _payment_status(role_urls, seed.payment_id) == "succeeded"
    assert await _subscription_status(role_urls, seed.subscription_id) == "active"
    # Exactly one ledger row and one effect.
    ledger = await _fetch(
        role_urls,
        "SELECT count(*) AS n FROM payment_webhooks WHERE dedup_key = :k",
        {"k": "txn-r:confirm"},
    )
    assert ledger[0]["n"] == 1


async def test_concurrent_delivery_single_effect(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="pending", provider_txn_id="txn-c")
    proc = _processor(maintenance_session_factory, test_settings)
    raw = _payme_raw("PerformTransaction", {"id": "txn-c"})

    results = await asyncio.gather(proc.process("payme", raw), proc.process("payme", raw))

    assert all("result" in body for _, body in results)  # both answered in-dialect
    assert await _payment_status(role_urls, seed.payment_id) == "succeeded"
    ledger = await _fetch(
        role_urls,
        "SELECT count(*) AS n FROM payment_webhooks WHERE dedup_key = :k",
        {"k": "txn-c:confirm"},
    )
    assert ledger[0]["n"] == 1  # concurrent inserts serialized to one row


async def test_cross_tenant_binding_from_our_records(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    a = await _seed(role_urls, payment_status="pending", provider_txn_id="a-txn")
    b = await _seed(role_urls, payment_status="pending", provider_txn_id="b-txn")
    proc = _processor(maintenance_session_factory, test_settings)

    # Confirm A's transaction: only A moves; B is untouched (tenant from our record).
    await proc.process("payme", _payme_raw("PerformTransaction", {"id": "a-txn"}))
    assert await _payment_status(role_urls, a.payment_id) == "succeeded"
    assert await _subscription_status(role_urls, a.subscription_id) == "active"
    assert await _payment_status(role_urls, b.payment_id) == "pending"
    assert await _subscription_status(role_urls, b.subscription_id) == "pending"

    # A callback for an unknown transaction binds to no tenant -> not_found.
    _, body = await proc.process("payme", _payme_raw("PerformTransaction", {"id": "ghost-txn"}))
    assert _err_code(body) == PAYME_ERR_ORDER_NOT_FOUND


async def test_amount_mismatch_blocks_transition(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    seed = await _seed(role_urls, payment_status="created")
    proc = _processor(maintenance_session_factory, test_settings)
    # The classic x100 bug: send sums where Payme expects tiyin (amount, not amount*100).
    params = {"id": "txn-m", "account": {"payment_id": str(seed.payment_id)}, "amount": seed.amount}

    _, body = await proc.process("payme", _payme_raw("CreateTransaction", params))

    assert _err_code(body) == PAYME_ERR_INVALID_AMOUNT
    assert await _payment_status(role_urls, seed.payment_id) == "created"  # no transition


async def test_succeeded_cannot_be_paid_again(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    role_urls: dict[str, str],
) -> None:
    # Already succeeded; a fresh perform for the same transaction must not re-pay.
    seed = await _seed(role_urls, payment_status="succeeded", provider_txn_id="done-txn")
    proc = _processor(maintenance_session_factory, test_settings)

    _, body = await proc.process("payme", _payme_raw("PerformTransaction", {"id": "done-txn"}))

    assert _err_code(body) == PAYME_ERR_UNABLE_TO_PERFORM  # invalid_state
    assert await _payment_status(role_urls, seed.payment_id) == "succeeded"


async def test_unrecognizable_request_raises_verification_error(
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
) -> None:
    from shared.errors import WebhookVerificationError

    proc = _processor(maintenance_session_factory, test_settings)
    with pytest.raises(WebhookVerificationError):
        await proc.process("payme", RawWebhook(headers={}, body="not json"))


# ---------------------------------------------------- HTTP route (end-to-end)


def _app_settings(role_urls: dict[str, str], redis_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        log_level="WARNING",
        database_url=role_urls[ROLE_USER],
        database_migrator_url=role_urls[ROLE_MIGRATOR],
        database_maintenance_url=role_urls[ROLE_MAINTENANCE],
        redis_url=redis_url,
        enabled_payment_providers="payme",
        payme_merchant_id="merchant-1",
        payme_merchant_key=PAYME_KEY,
    )


def _create_body(seed: SimpleNamespace, *, rpc_id: int = 1) -> str:
    return json.dumps(
        {
            "method": "CreateTransaction",
            "params": {
                "id": f"rt-{rpc_id}",
                "account": {"payment_id": str(seed.payment_id)},
                "amount": seed.amount * 100,
            },
            "id": rpc_id,
        }
    )


def test_route_marks_pending_end_to_end(role_urls: dict[str, str], redis_url: str) -> None:
    seed = asyncio.run(_seed(role_urls, payment_status="created"))
    app = create_app(_app_settings(role_urls, redis_url))
    with TestClient(app) as client:
        resp = client.post(
            "/api/billing/webhooks/payme",
            content=_create_body(seed),
            headers={"Authorization": _payme_auth(), "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    assert "result" in resp.json()  # Payme JSON-RPC success dialect
    assert asyncio.run(_payment_status(role_urls, seed.payment_id)) == "pending"


def test_route_bad_signature_answers_in_dialect_not_403(
    role_urls: dict[str, str], redis_url: str
) -> None:
    seed = asyncio.run(_seed(role_urls, payment_status="created"))
    app = create_app(_app_settings(role_urls, redis_url))
    with TestClient(app) as client:
        resp = client.post(
            "/api/billing/webhooks/payme",
            content=_create_body(seed),
            headers={"Authorization": _payme_auth("wrong"), "Content-Type": "application/json"},
        )
    # Answered in the provider dialect with HTTP 200 — webhooks are excluded from
    # the generic DomainError->HTTP handler that would have returned 403.
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == PAYME_ERR_INSUFFICIENT_PRIVILEGE
    assert asyncio.run(_payment_status(role_urls, seed.payment_id)) == "created"


def test_route_unrecognizable_request_returns_403(
    role_urls: dict[str, str], redis_url: str
) -> None:
    app = create_app(_app_settings(role_urls, redis_url))
    with TestClient(app) as client:
        resp = client.post(
            "/api/billing/webhooks/payme",
            content="garbage",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 403  # no dialect could be determined -> fallback
