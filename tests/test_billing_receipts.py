"""Billing receipt subscriber + template parity (Phase 3, Task 16).

The reliable subscriber turns a billing.payment.succeeded event into an outbox
row addressed to the tenant owner's email. Also asserts the template parity
invariant: every registered notification template has both ru and uz files.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.billing.receipts import send_payment_receipt
from core.notifications.models import NotificationOutbox
from core.notifications.registry import template_registry
from shared.context import Actor, TenantContext
from shared.db_provisioning import ROLE_MIGRATOR
from shared.events import EventBus, EventEnvelope
from shared.handler_runtime import HandlerRuntime, reset_handler_runtime, set_handler_runtime
from shared.i18n import SUPPORTED_LOCALES
from shared.ids import new_uuid7
from shared.service import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration


async def _seed_owner_tenant(role_urls: dict[str, str]) -> tuple[UUID, str]:
    tenant_id, user_id = uuid4(), uuid4()
    email = f"{user_id}@example.uz"
    engine = create_async_engine(role_urls[ROLE_MIGRATOR])
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
                {"id": user_id, "email": email},
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
    return tenant_id, email


async def test_payment_succeeded_creates_receipt_for_owner(
    role_urls: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id, owner_email = await _seed_owner_tenant(role_urls)
    payment_id = str(uuid4())
    event = EventEnvelope(
        event_id=new_uuid7(),
        name="billing.payment.succeeded",
        version=1,
        occurred_at=datetime.now(UTC),
        tenant_id=None,  # value not used; the runtime ctx supplies the tenant
        actor=Actor(kind="system", id="payme"),
        payload={"payment_id": payment_id, "amount": 50000, "currency": "UZS"},
    )
    ctx = TenantContext(
        tenant_id=tenant_id,
        actor=Actor(kind="system", id="billing.receipt"),
        request_id=None,
    )
    async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
        token = set_handler_runtime(HandlerRuntime(uow=uow, ctx=ctx, bus=EventBus()))
        try:
            await send_payment_receipt(event)
        finally:
            reset_handler_runtime(token)

    async with SqlAlchemyUnitOfWork(session_factory, context=ctx) as uow:
        rows = (await uow.session.execute(select(NotificationOutbox))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.channel == "email"
    assert row.recipient == owner_email
    assert row.template_key == "billing.payment_succeeded"
    assert row.params["amount"] == 50000
    assert row.params["currency"] == "UZS"
    assert row.dedup_key == f"receipt:payment:{payment_id}"


def test_every_registered_template_has_all_locales() -> None:
    templates = template_registry.all_templates()
    assert templates  # billing registered its receipt templates at import
    for tdef in templates:
        base = template_registry.dir_for(tdef.key)
        for locale in SUPPORTED_LOCALES:
            assert (base / locale / f"{tdef.key}.txt").exists(), f"{tdef.key}:{locale}"
