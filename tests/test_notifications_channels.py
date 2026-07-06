"""Channel adapter unit tests (interfaces §4.2) — no network, no Docker.

httpx.MockTransport drives Telegram/Eskiz without real HTTP. Covers: dormant
no-op, phone normalization, address masking, success parsing, permanent vs
temporary mapping, Eskiz 401 transparent re-auth, and the SMS daily cap.
"""

from typing import cast
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis

from core.notifications.channel_config import mask_address
from core.notifications.channels import build_channel_from_config, build_notification_channels
from core.notifications.channels.email import EmailChannel
from core.notifications.channels.eskiz import EskizSmsChannel, normalize_uz_phone
from core.notifications.channels.telegram import TelegramChannel
from core.notifications.ports import ChannelPermanentError, ChannelTemporaryError, RenderedMessage
from core.notifications.sms_cap import SmsDailyCap
from shared.config import Settings


def _msg(address: str, body: str = "hello") -> RenderedMessage:
    return RenderedMessage(
        notification_id=uuid4(), address=address, subject=None, body=body, locale="ru"
    )


# --- pure helpers ---


def test_normalize_uz_phone_variants() -> None:
    assert normalize_uz_phone("+998 90 123-45-67") == "998901234567"
    assert normalize_uz_phone("998901234567") == "998901234567"
    assert normalize_uz_phone("901234567") == "998901234567"
    with pytest.raises(ChannelPermanentError):
        normalize_uz_phone("12345")


def test_mask_address() -> None:
    assert mask_address("buyer@example.uz").endswith("@example.uz")
    assert "buyer" not in mask_address("buyer@example.uz")
    assert mask_address("998901234567") == "9989***67"
    assert mask_address("abcd") == "***"


# --- dormant no-op (no credentials) ---


async def test_dormant_channels_are_noops() -> None:
    telegram = TelegramChannel(None)
    eskiz = EskizSmsChannel(None, None)
    email = EmailChannel(host=None, port=587, username=None, password=None, from_address="x@y.uz")
    assert telegram.configured is False
    assert eskiz.configured is False
    assert email.configured is False
    assert (await telegram.send(_msg("123"))).provider_message_id is None
    assert (await eskiz.send(_msg("998901234567"))).provider_message_id is None
    assert (await email.send(_msg("a@b.uz"))).provider_message_id is None


def test_build_platform_channels_dormant_without_creds() -> None:
    channels = build_notification_channels(Settings(_env_file=None))
    assert set(channels) == {"telegram", "sms_eskiz", "email"}
    assert all(not channel.configured for channel in channels.values())


def test_build_channel_from_config_selects_and_configures() -> None:
    telegram = build_channel_from_config("telegram", {"bot_token": "t"})
    assert isinstance(telegram, TelegramChannel)
    assert telegram.configured is True
    sms = build_channel_from_config("sms_eskiz", {"email": "e@x.uz", "password": "p"})
    assert isinstance(sms, EskizSmsChannel)
    assert sms.configured is True
    email = build_channel_from_config(
        "email",
        {
            "host": "smtp.x.uz",
            "port": "465",
            "username": "u",
            "password": "p",
            "from_address": "f@x.uz",
        },
    )
    assert isinstance(email, EmailChannel)
    assert email.configured is True
    with pytest.raises(ValueError, match="unknown notification channel"):
        build_channel_from_config("carrier_pigeon", {})


# --- Telegram ---


async def test_telegram_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sendMessage")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    channel = TelegramChannel("tok", transport=httpx.MockTransport(handler))
    result = await channel.send(_msg("chat-1"))
    assert result.provider_message_id == "42"


async def test_telegram_bad_chat_is_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "chat not found"})

    channel = TelegramChannel("tok", transport=httpx.MockTransport(handler))
    with pytest.raises(ChannelPermanentError):
        await channel.send(_msg("chat-1"))


async def test_telegram_5xx_is_temporary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    channel = TelegramChannel("tok", transport=httpx.MockTransport(handler))
    with pytest.raises(ChannelTemporaryError):
        await channel.send(_msg("chat-1"))


# --- Eskiz ---


async def test_eskiz_success_authenticates_then_sends() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"data": {"token": "tok-1"}})
        assert request.headers["Authorization"] == "Bearer tok-1"
        return httpx.Response(200, json={"id": "555", "status": "waiting"})

    channel = EskizSmsChannel("e@x.uz", "pw", transport=httpx.MockTransport(handler))
    result = await channel.send(_msg("998901234567"))
    assert result.provider_message_id == "555"
    assert calls[0].endswith("/auth/login")


async def test_eskiz_401_triggers_transparent_reauth() -> None:
    sends = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"data": {"token": "fresh"}})
        sends["n"] += 1
        if request.headers["Authorization"] == "Bearer fresh":
            return httpx.Response(200, json={"id": "777", "status": "waiting"})
        return httpx.Response(401, json={"message": "token invalid"})

    channel = EskizSmsChannel("e@x.uz", "pw", transport=httpx.MockTransport(handler))
    channel._token = "stale"  # prime a stale token to force the 401 path
    result = await channel.send(_msg("998901234567"))
    assert result.provider_message_id == "777"
    assert sends["n"] == 2  # first 401, then success after re-auth


async def test_eskiz_bad_phone_is_permanent() -> None:
    channel = EskizSmsChannel(
        "e@x.uz", "pw", transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    with pytest.raises(ChannelPermanentError):
        await channel.send(_msg("not-a-phone"))


# --- SMS daily cap ---


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> None:
        return None


async def test_sms_daily_cap_blocks_beyond_limit() -> None:
    cap = SmsDailyCap(cast(Redis, _FakeRedis()), cap_per_tenant=2)
    tenant = uuid4()
    assert await cap.try_consume(tenant) is True
    assert await cap.try_consume(tenant) is True
    assert await cap.try_consume(tenant) is False  # third exceeds the cap of 2
    # A different tenant has its own bucket.
    assert await cap.try_consume(uuid4()) is True


async def test_sms_daily_cap_zero_means_unlimited() -> None:
    cap = SmsDailyCap(cast(Redis, _FakeRedis()), cap_per_tenant=0)
    for _ in range(10):
        assert await cap.try_consume(uuid4()) is True
