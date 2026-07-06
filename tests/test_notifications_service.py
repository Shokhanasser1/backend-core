"""NotificationService integration tests (interfaces §3.4, Phase 3 Task 15).

Against a real Postgres (RLS + outbox). Verifies:
- send() writes one outbox row per resolved channel under a shared notification_id;
- AddressRecipient uses the given address; UserRecipient resolves email/phone +
  locale from the user profile;
- locale chain (explicit -> user -> ambient -> 'ru');
- dedup_key idempotency returns the existing notification_id, no duplicate rows;
- get_status aggregates queued / sent / partially_failed;
- template rendering substitutes params per locale;
- set_channel_config stores an ENCRYPTED blob (write-only) and get_channel_status
  reports configured/enabled without leaking the secret.
"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.notifications.models import NotificationOutbox, NotificationSetting
from core.notifications.registry import TemplateDef, TemplateRegistry
from core.notifications.schemas import AddressRecipient, UserRecipient
from core.notifications.service import NotificationService
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MIGRATOR
from shared.encryption import SecretCipher
from shared.errors import InvariantViolationError, NotFoundError
from shared.events import EventBus
from shared.service import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration


async def _seed_user_tenant(
    role_urls: dict[str, str], *, locale: str, phone: str | None
) -> tuple[UUID, UUID, str]:
    tenant_id, user_id = uuid4(), uuid4()
    email = f"{user_id}@example.uz"
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, phone, locale) "
                    "VALUES (:id, :email, 'x', :phone, :locale)"
                ),
                {"id": user_id, "email": email, "phone": phone, "locale": locale},
            )
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, owner_user_id) "
                    "VALUES (:id, 'Test', :slug, :owner)"
                ),
                {"id": tenant_id, "slug": str(tenant_id), "owner": user_id},
            )
    finally:
        await engine.dispose()
    return tenant_id, user_id, email


def _write_template(base: Path, key: str, ru: str, uz: str) -> None:
    for locale, body in (("ru", ru), ("uz", uz)):
        target = base / locale
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{key}.txt").write_text(body, encoding="utf-8")


@pytest.fixture
def notif_registry(tmp_path: Path) -> TemplateRegistry:
    _write_template(tmp_path, "test.hello", "Привет, $name!", "Salom, $name!")
    _write_template(tmp_path, "test.multi", "Многоканал $x", "Ko'p kanal $x")
    registry = TemplateRegistry()
    registry.register(
        "test",
        [
            TemplateDef("test.hello", ("email",), frozenset({"name"})),
            TemplateDef("test.multi", ("email", "sms_eskiz")),
        ],
        tmp_path,
    )
    return registry


def _service(
    uow: SqlAlchemyUnitOfWork, ctx: TenantContext, registry: TemplateRegistry
) -> NotificationService:
    from core.auth.directory import UserDirectory

    return NotificationService(
        uow,
        EventBus(),
        ctx,
        registry=registry,
        cipher=SecretCipher(()),
        directory=UserDirectory(uow.session),
    )


@pytest.fixture
async def notif_ctx(role_urls: dict[str, str], _clean_db: None) -> TenantContext:
    tenant_id, user_id, _ = await _seed_user_tenant(role_urls, locale="uz", phone="+998901234567")
    return TenantContext(
        tenant_id=tenant_id, actor=Actor(kind="user", id=str(user_id)), request_id="req-notif"
    )


async def test_tenant_isolation_of_outbox_and_settings(
    session_factory: async_sessionmaker[AsyncSession],
    role_urls: dict[str, str],
    _clean_db: None,
    notif_registry: TemplateRegistry,
) -> None:
    """RLS second line: tenant B sees neither A's outbox rows nor A's channel
    config (schema §2.4, new tables notification_outbox + notification_settings)."""
    t_a, u_a, _ = await _seed_user_tenant(role_urls, locale="ru", phone=None)
    t_b, u_b, _ = await _seed_user_tenant(role_urls, locale="ru", phone=None)
    ctx_a = TenantContext(tenant_id=t_a, actor=Actor(kind="user", id=str(u_a)), request_id="a")
    ctx_b = TenantContext(tenant_id=t_b, actor=Actor(kind="user", id=str(u_b)), request_id="b")

    async with SqlAlchemyUnitOfWork(session_factory, context=ctx_a) as uow:
        service_a = _service(uow, ctx_a, notif_registry)
        nid = await service_a.send(AddressRecipient("email", "a@x.uz"), "test.hello", {"name": "A"})
        await service_a.set_channel_config("telegram", {"bot_token": "secret-A"})

    async with SqlAlchemyUnitOfWork(session_factory, context=ctx_b) as uow:
        service_b = _service(uow, ctx_b, notif_registry)
        with pytest.raises(NotFoundError):  # A's outbox rows are invisible to B
            await service_b.get_status(nid)
        status_b = await service_b.get_channel_status("telegram")
    assert status_b.configured is False  # A's channel config is invisible to B


async def test_send_address_recipient_writes_one_row(
    session_factory: async_sessionmaker[AsyncSession],
    notif_ctx: TenantContext,
    notif_registry: TemplateRegistry,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        service = _service(uow, notif_ctx, notif_registry)
        nid = await service.send(
            AddressRecipient("email", "buyer@example.uz"),
            "test.hello",
            {"name": "Ali"},
            locale="ru",
        )

    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        rows = (await uow.session.execute(select(NotificationOutbox))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.notification_id == nid
    assert row.channel == "email"
    assert row.recipient == "buyer@example.uz"
    assert row.template_key == "test.hello"
    assert row.locale == "ru"
    assert row.status == "pending"
    assert row.params == {"name": "Ali"}
    assert row.tenant_id == notif_ctx.tenant_id


async def test_send_user_recipient_resolves_profile_addresses_and_locale(
    session_factory: async_sessionmaker[AsyncSession],
    notif_ctx: TenantContext,
    notif_registry: TemplateRegistry,
) -> None:
    user_id = UUID(str(notif_ctx.actor.id))
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        service = _service(uow, notif_ctx, notif_registry)
        nid = await service.send(UserRecipient(user_id), "test.multi", {"x": "1"})

    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        rows = (
            (
                await uow.session.execute(
                    select(NotificationOutbox).where(NotificationOutbox.notification_id == nid)
                )
            )
            .scalars()
            .all()
        )
    by_channel = {row.channel: row for row in rows}
    assert set(by_channel) == {"email", "sms_eskiz"}
    assert by_channel["sms_eskiz"].recipient == "+998901234567"
    # Locale not passed -> user profile locale ('uz').
    assert all(row.locale == "uz" for row in rows)


async def test_dedup_key_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    notif_ctx: TenantContext,
    notif_registry: TemplateRegistry,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        service = _service(uow, notif_ctx, notif_registry)
        first = await service.send(
            AddressRecipient("email", "a@example.uz"), "test.hello", {"name": "A"}, dedup_key="k1"
        )
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        service = _service(uow, notif_ctx, notif_registry)
        second = await service.send(
            AddressRecipient("email", "a@example.uz"), "test.hello", {"name": "A"}, dedup_key="k1"
        )
    assert first == second
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        count = (
            await uow.session.execute(select(func.count()).select_from(NotificationOutbox))
        ).scalar_one()
    assert count == 1


async def test_get_status_aggregation(
    session_factory: async_sessionmaker[AsyncSession],
    notif_ctx: TenantContext,
    notif_registry: TemplateRegistry,
    role_urls: dict[str, str],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        service = _service(uow, notif_ctx, notif_registry)
        nid = await service.send(
            UserRecipient(UUID(str(notif_ctx.actor.id))), "test.multi", {"x": "1"}
        )
        status = await service.get_status(nid)
    assert status.status == "queued"

    # Force one row sent, one dead -> partially_failed (owner bypasses RLS).
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE notification_outbox SET status='sent' "
                    "WHERE notification_id=:n AND channel='email'"
                ),
                {"n": nid},
            )
            await conn.execute(
                text(
                    "UPDATE notification_outbox SET status='dead' "
                    "WHERE notification_id=:n AND channel='sms_eskiz'"
                ),
                {"n": nid},
            )
    finally:
        await engine.dispose()

    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        service = _service(uow, notif_ctx, notif_registry)
        status = await service.get_status(nid)
    assert status.status == "partially_failed"


async def test_send_validation_errors(
    session_factory: async_sessionmaker[AsyncSession],
    notif_ctx: TenantContext,
    notif_registry: TemplateRegistry,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        service = _service(uow, notif_ctx, notif_registry)
        with pytest.raises(NotFoundError):
            await service.send(AddressRecipient("email", "a@b.uz"), "test.unknown", {})
        with pytest.raises(InvariantViolationError):  # missing required 'name'
            await service.send(AddressRecipient("email", "a@b.uz"), "test.hello", {})
        with pytest.raises(InvariantViolationError):  # no address for requested channel
            await service.send(
                AddressRecipient("telegram", "123"), "test.hello", {"name": "A"}, channels=["email"]
            )


def test_template_rendering_per_locale(notif_registry: TemplateRegistry) -> None:
    assert notif_registry.render("test.hello", "ru", {"name": "Ali"}).body == "Привет, Ali!"
    assert notif_registry.render("test.hello", "uz", {"name": "Ali"}).body == "Salom, Ali!"


async def test_set_channel_config_is_encrypted_and_write_only(
    session_factory: async_sessionmaker[AsyncSession],
    notif_ctx: TenantContext,
    notif_registry: TemplateRegistry,
) -> None:
    secret = "super-secret-bot-token"
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        service = _service(uow, notif_ctx, notif_registry)
        await service.set_channel_config("telegram", {"bot_token": secret})
        status = await service.get_channel_status("telegram")
    assert status.configured is True
    assert status.is_enabled is True
    # The DTO carries no secret field at all.
    assert secret not in status.model_dump_json()

    # Stored blob is ciphertext, not plaintext; decrypts back with the same key.
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        setting = (await uow.session.execute(select(NotificationSetting))).scalar_one()
    assert secret.encode() not in bytes(setting.config_encrypted)
    assert secret in SecretCipher(()).decrypt(bytes(setting.config_encrypted))


async def test_get_channel_status_unconfigured(
    session_factory: async_sessionmaker[AsyncSession],
    notif_ctx: TenantContext,
    notif_registry: TemplateRegistry,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, context=notif_ctx) as uow:
        service = _service(uow, notif_ctx, notif_registry)
        status = await service.get_channel_status("email")
        with pytest.raises(NotFoundError):
            await service.get_channel_status("carrier_pigeon")
    assert status.configured is False
    assert status.is_enabled is False
