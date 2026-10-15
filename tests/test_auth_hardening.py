"""Auth hardening (audit batch 2): a blocked user cannot refresh or finish 2FA,
the tenant-token exchange mints no long-lived credential, a recovery code is
single-use under concurrency, registration is rate-limited per IP, and auth
events carry no email (PII)."""

import asyncio
import threading
from typing import Any, cast

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.main import create_app
from core.auth.security.totp import hash_recovery_code
from shared.db_provisioning import ROLE_MIGRATOR
from tests.test_auth_flows import PASSWORD, _headers, _login, _owner_with_tenant, _register

pytestmark = pytest.mark.integration


async def _sql(role_urls: dict[str, str], sql: str, params: dict[str, object]) -> Any:
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return result.scalar() if result.returns_rows else None
    finally:
        await engine.dispose()


def _set_user_status(role_urls: dict[str, str], email: str, status: str) -> None:
    asyncio.run(
        _sql(role_urls, "UPDATE users SET status = :s WHERE email = :e", {"s": status, "e": email})
    )


def _enroll_2fa(client: TestClient, email: str) -> tuple[str, list[str]]:
    token = _login(client, email)["access_token"]
    secret = client.post("/api/auth/2fa/enable", headers=_headers(token)).json()["secret"]
    codes = client.post(
        "/api/auth/2fa/confirm", headers=_headers(token), json={"code": pyotp.TOTP(secret).now()}
    ).json()["codes"]
    return secret, codes


def _challenge(client: TestClient, email: str) -> str:
    body = client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).json()
    return cast("str", body["challenge_token"])


def test_blocked_user_cannot_refresh(client: TestClient, role_urls: dict[str, str]) -> None:
    _register(client, "blk@example.uz")
    tokens = _login(client, "blk@example.uz")

    _set_user_status(role_urls, "blk@example.uz", "blocked")
    blocked = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert blocked.status_code == 401

    # The session family was revoked, not just refused: unblocking does not revive it.
    _set_user_status(role_urls, "blk@example.uz", "active")
    again = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == 401


def test_blocked_user_cannot_complete_two_factor(
    client: TestClient, role_urls: dict[str, str]
) -> None:
    _register(client, "blk2@example.uz")
    secret, _codes = _enroll_2fa(client, "blk2@example.uz")
    challenge = _challenge(client, "blk2@example.uz")

    _set_user_status(role_urls, "blk2@example.uz", "blocked")
    resp = client.post(
        "/api/auth/2fa/verify",
        json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
    )
    assert resp.status_code == 401


def test_tenant_token_exchange_returns_access_only(
    client: TestClient, role_urls: dict[str, str]
) -> None:
    user_token, tenant_id, _ = _owner_with_tenant(client, "tt@example.uz")
    before = asyncio.run(_sql(role_urls, "SELECT count(*) FROM refresh_tokens", {}))

    resp = client.post(f"/api/auth/tenants/{tenant_id}/token", headers=_headers(user_token))

    assert resp.status_code == 200
    assert set(resp.json()) == {"access_token", "token_type", "expires_in"}
    after = asyncio.run(_sql(role_urls, "SELECT count(*) FROM refresh_tokens", {}))
    assert after == before  # no new 30-day session minted from a short-lived token


def test_recovery_code_is_single_use_under_concurrency(
    client: TestClient, role_urls: dict[str, str]
) -> None:
    _register(client, "rc@example.uz")
    _secret, codes = _enroll_2fa(client, "rc@example.uz")
    challenge = _challenge(client, "rc@example.uz")
    locked = threading.Event()

    def spend_concurrently() -> None:
        """Another login spends the same code and commits only after our request
        has read it as unused."""

        async def run() -> None:
            engine = create_async_engine(role_urls[ROLE_MIGRATOR])
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("UPDATE user_recovery_codes SET used_at = now() WHERE code_hash = :h"),
                        {"h": hash_recovery_code(codes[0])},
                    )
                    locked.set()
                    await asyncio.sleep(0.7)
            finally:
                await engine.dispose()

        asyncio.run(run())

    holder = threading.Thread(target=spend_concurrently)
    holder.start()
    assert locked.wait(10)
    resp = client.post(
        "/api/auth/2fa/verify", json={"challenge_token": challenge, "code": codes[0]}
    )
    holder.join()

    assert resp.status_code == 401


def test_register_is_rate_limited_per_ip(test_settings: Settings, _clean_db: None) -> None:
    app = create_app(test_settings.model_copy(update={"register_ip_limit_per_hour": 2}))
    with TestClient(app) as small:
        for n in range(2):
            _register(small, f"r{n}@example.uz")
        resp = small.post(
            "/api/auth/register", json={"email": "r9@example.uz", "password": PASSWORD}
        )
    assert resp.status_code == 429


def test_auth_events_carry_no_email(client: TestClient) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    app = cast("FastAPI", client.app)
    original = app.state.bus.publish

    async def spy(envelope: Any) -> None:
        captured.append((envelope.name, dict(envelope.payload)))
        await original(envelope)

    app.state.bus.publish = spy
    try:
        _register(client, "quiet@example.uz")
        client.post("/api/auth/login", json={"email": "quiet@example.uz", "password": "nope"})
        client.post("/api/auth/login", json={"email": "ghost@example.uz", "password": "nope"})
    finally:
        app.state.bus.publish = original

    names = {name for name, _ in captured}
    assert {"auth.user.registered", "auth.user.login_failed"} <= names
    for _name, payload in captured:
        assert "email" not in payload
        assert not any("@" in str(value) for value in payload.values())
