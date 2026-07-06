"""Outbox dispatcher integration tests (schema §2.4, interfaces §4.2).

Against a real Postgres (RLS/maintenance) + real Redis (SMS cap), with FAKE
channels. Verifies the delivery contract: success -> sent; ChannelPermanentError
-> dead + notifications.message.failed; transient -> failed with backoff;
exhausted attempts -> dead; and the per-tenant SMS daily cap.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.notifications.dispatcher import dispatch_due_notifications
from core.notifications.ports import (
    ChannelPermanentError,
    ChannelResult,
    ChannelTemporaryError,
    NotificationChannel,
    RenderedMessage,
)
from core.notifications.registry import TemplateDef, TemplateRegistry
from shared.config import Settings
from shared.db_provisioning import ROLE_MIGRATOR
from shared.encryption import SecretCipher
from shared.events import EventBus, EventEnvelope

pytestmark = pytest.mark.integration


class FakeChannel:
    def __init__(self, code: str, *, configured: bool = True, behavior: str = "ok") -> None:
        self.code = code
        self._configured = configured
        self._behavior = behavior
        self.sent: list[RenderedMessage] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def send(self, message: RenderedMessage) -> ChannelResult:
        self.sent.append(message)
        if self._behavior == "permanent":
            raise ChannelPermanentError("bad address")
        if self._behavior == "temporary":
            raise ChannelTemporaryError("try later")
        return ChannelResult(provider_message_id="fake-msg-1")


def _channels(**kw: FakeChannel) -> dict[str, NotificationChannel]:
    return cast("dict[str, NotificationChannel]", kw)


async def _seed_tenant(role_urls: dict[str, str]) -> UUID:
    tenant_id, user_id = uuid4(), uuid4()
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
                {"id": user_id, "email": f"{user_id}@example.uz"},
            )
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, owner_user_id) "
                    "VALUES (:id, 'T', :slug, :owner)"
                ),
                {"id": tenant_id, "slug": str(tenant_id), "owner": user_id},
            )
    finally:
        await engine.dispose()
    return tenant_id


async def _seed_row(
    role_urls: dict[str, str],
    tenant_id: UUID,
    *,
    channel: str = "email",
    recipient: str = "buyer@example.uz",
    attempts: int = 0,
    template_key: str = "test.msg",
) -> UUID:
    row_id, notification_id = uuid4(), uuid4()
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO notification_outbox "
                    "(id, notification_id, tenant_id, channel, recipient, template_key, locale, "
                    " params, status, attempts, next_retry_at) "
                    "VALUES (:id, :nid, :tid, :ch, :rcpt, :tpl, 'ru', CAST('{}' AS jsonb), "
                    " 'pending', :att, :due)"
                ),
                {
                    "id": row_id,
                    "nid": notification_id,
                    "tid": tenant_id,
                    "ch": channel,
                    "rcpt": recipient,
                    "tpl": template_key,
                    "att": attempts,
                    "due": datetime.now(UTC) - timedelta(minutes=1),
                },
            )
    finally:
        await engine.dispose()
    return row_id


async def _row(role_urls: dict[str, str], row_id: UUID) -> dict[str, object]:
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT status, attempts, next_retry_at, last_error, provider_message_id "
                    "FROM notification_outbox WHERE id = :id"
                ),
                {"id": row_id},
            )
            return dict(result.mappings().one())
    finally:
        await engine.dispose()


@pytest.fixture
def dispatch_registry(tmp_path: Path) -> TemplateRegistry:
    for locale, body in (("ru", "Сообщение"), ("uz", "Xabar")):
        target = tmp_path / locale
        target.mkdir(parents=True, exist_ok=True)
        (target / "test.msg.txt").write_text(body, encoding="utf-8")
    registry = TemplateRegistry()
    registry.register("test", [TemplateDef("test.msg", ("email", "sms_eskiz"))], tmp_path)
    return registry


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(redis_url)
    yield client
    await client.aclose()


def _capturing_bus() -> tuple[EventBus, list[EventEnvelope]]:
    bus = EventBus()
    captured: list[EventEnvelope] = []

    @bus.subscribe("notifications.message.failed")
    async def _capture(event: EventEnvelope) -> None:
        captured.append(event)

    return bus, captured


async def test_success_marks_sent(
    role_urls: dict[str, str],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    dispatch_registry: TemplateRegistry,
    test_settings: Settings,
) -> None:
    tenant_id = await _seed_tenant(role_urls)
    row_id = await _seed_row(role_urls, tenant_id)
    channel = FakeChannel("email")
    bus, _ = _capturing_bus()

    processed = await dispatch_due_notifications(
        maintenance_session_factory,
        redis_client,
        bus,
        test_settings,
        SecretCipher(()),
        dispatch_registry,
        _channels(email=channel),
    )
    assert processed == 1
    row = await _row(role_urls, row_id)
    assert row["status"] == "sent"
    assert row["provider_message_id"] == "fake-msg-1"
    assert len(channel.sent) == 1
    assert channel.sent[0].body == "Сообщение"


async def test_permanent_error_dead_letters_and_emits_event(
    role_urls: dict[str, str],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    dispatch_registry: TemplateRegistry,
    test_settings: Settings,
) -> None:
    tenant_id = await _seed_tenant(role_urls)
    row_id = await _seed_row(role_urls, tenant_id)
    bus, captured = _capturing_bus()

    await dispatch_due_notifications(
        maintenance_session_factory,
        redis_client,
        bus,
        test_settings,
        SecretCipher(()),
        dispatch_registry,
        _channels(email=FakeChannel("email", behavior="permanent")),
    )
    row = await _row(role_urls, row_id)
    assert row["status"] == "dead"
    assert len(captured) == 1
    assert captured[0].name == "notifications.message.failed"
    assert captured[0].payload["channel"] == "email"
    # recipient is masked in the event payload (threat model).
    assert captured[0].payload["recipient"] != "buyer@example.uz"


async def test_transient_error_reschedules_with_backoff(
    role_urls: dict[str, str],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    dispatch_registry: TemplateRegistry,
    test_settings: Settings,
) -> None:
    tenant_id = await _seed_tenant(role_urls)
    row_id = await _seed_row(role_urls, tenant_id)
    bus, captured = _capturing_bus()

    await dispatch_due_notifications(
        maintenance_session_factory,
        redis_client,
        bus,
        test_settings,
        SecretCipher(()),
        dispatch_registry,
        _channels(email=FakeChannel("email", behavior="temporary")),
    )
    row = await _row(role_urls, row_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert not captured  # not dead yet, no failed event
    next_retry = row["next_retry_at"]
    assert isinstance(next_retry, datetime)
    assert next_retry > datetime.now(UTC)  # backoff into the future


async def test_transient_error_dead_letters_when_attempts_exhausted(
    role_urls: dict[str, str],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    dispatch_registry: TemplateRegistry,
    test_settings: Settings,
) -> None:
    tenant_id = await _seed_tenant(role_urls)
    # attempts already at max-1; the claim's increment reaches the max.
    row_id = await _seed_row(
        role_urls, tenant_id, attempts=test_settings.notification_max_attempts - 1
    )
    bus, captured = _capturing_bus()

    await dispatch_due_notifications(
        maintenance_session_factory,
        redis_client,
        bus,
        test_settings,
        SecretCipher(()),
        dispatch_registry,
        _channels(email=FakeChannel("email", behavior="temporary")),
    )
    row = await _row(role_urls, row_id)
    assert row["status"] == "dead"
    assert len(captured) == 1


async def test_sms_daily_cap_enforced(
    role_urls: dict[str, str],
    maintenance_session_factory: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    dispatch_registry: TemplateRegistry,
    test_settings: Settings,
) -> None:
    tenant_id = await _seed_tenant(role_urls)
    rows = [
        await _seed_row(role_urls, tenant_id, channel="sms_eskiz", recipient="998901234567")
        for _ in range(3)
    ]
    channel = FakeChannel("sms_eskiz")
    bus, _ = _capturing_bus()
    capped = test_settings.model_copy(update={"sms_daily_cap_per_tenant": 2})

    await dispatch_due_notifications(
        maintenance_session_factory,
        redis_client,
        bus,
        capped,
        SecretCipher(()),
        dispatch_registry,
        _channels(sms_eskiz=channel),
    )
    final = [await _row(role_urls, r) for r in rows]
    statuses = [row["status"] for row in final]
    assert statuses.count("sent") == 2  # cap of 2 delivered
    assert statuses.count("dead") == 1  # the third blocked by the cap
    assert len(channel.sent) == 2  # the capped one never reached the channel
    assert any("sms_daily_cap" in str(row["last_error"]) for row in final)
