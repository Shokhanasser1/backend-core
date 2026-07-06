"""Outbox dispatcher (schema §2.4 dispatch contract, interfaces §4.2).

Runs as app_maintenance (cross-tenant + platform rows). Each pass:
1. claims a batch of due rows with ``SELECT ... FOR UPDATE SKIP LOCKED`` and marks
   them 'sending' with a lease (a crashed worker's rows fall due again after the
   lease — no permanently stuck rows);
2. per row: render template -> resolve channel config (tenant settings, else
   platform env) -> enforce the SMS daily cap -> send via the adapter;
3. success -> 'sent'; ChannelPermanentError or exhausted attempts -> 'dead' +
   ``notifications.message.failed``; otherwise -> 'failed' with exponential backoff.

One bad row never blocks the batch: each is finalized in its own transaction and
unexpected errors are logged, leaving the row to fall due again after its lease.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.notifications.channel_config import mask_address
from core.notifications.channels import build_channel_from_config
from core.notifications.models import NotificationOutbox, NotificationSetting
from core.notifications.ports import ChannelPermanentError, NotificationChannel, RenderedMessage
from core.notifications.registry import TemplateRegistry
from core.notifications.sms_cap import SmsDailyCap
from shared.config import Settings
from shared.context import Actor, TenantContext
from shared.encryption import SecretCipher
from shared.errors import NotFoundError
from shared.events import EventBus, EventEnvelope
from shared.ids import new_uuid7
from shared.service import SqlAlchemyUnitOfWork

logger = logging.getLogger(__name__)

_DUE_STATES = ("pending", "failed", "sending")
_BACKOFF_BASE_SECONDS = 60  # 1 -> 2 -> 4 -> 8 -> 16 min (interfaces §4.2)
_BACKOFF_CAP_SECONDS = 16 * 60
_SMS_CHANNEL = "sms_eskiz"


def _system_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=None, actor=Actor(kind="system", id="notifications.dispatcher"), request_id=None
    )


@dataclass(frozen=True, slots=True)
class _ClaimedRow:
    id: UUID
    notification_id: UUID
    tenant_id: UUID | None
    channel: str
    recipient: str
    template_key: str
    locale: str
    params: dict[str, Any]
    attempts: int  # value AFTER this claim's increment


async def dispatch_due_notifications(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    redis: Any,
    bus: EventBus,
    settings: Settings,
    cipher: SecretCipher,
    registry: TemplateRegistry,
    channels: dict[str, NotificationChannel],
    *,
    batch: int = 50,
) -> int:
    """One dispatch pass. Returns the number of rows processed (attempted)."""
    claimed = await _claim_batch(maintenance_sessions, batch, settings.notification_lease_seconds)
    if not claimed:
        return 0
    cap = SmsDailyCap(redis, settings.sms_daily_cap_per_tenant)
    for row in claimed:
        try:
            await _process_row(
                maintenance_sessions, bus, settings, cipher, registry, channels, cap, row
            )
        except Exception:  # never let one row wedge the batch; lease re-dues it
            logger.exception("notification dispatch row failed", extra={"row_id": str(row.id)})
    return len(claimed)


async def _claim_batch(
    maintenance_sessions: async_sessionmaker[AsyncSession], batch: int, lease_seconds: int
) -> list[_ClaimedRow]:
    now = datetime.now(UTC)
    async with SqlAlchemyUnitOfWork(maintenance_sessions, context=_system_ctx()) as uow:
        rows = (
            (
                await uow.session.execute(
                    select(NotificationOutbox)
                    .where(
                        NotificationOutbox.status.in_(_DUE_STATES),
                        NotificationOutbox.next_retry_at <= now,
                    )
                    .order_by(NotificationOutbox.next_retry_at)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        claimed: list[_ClaimedRow] = []
        lease_deadline = now + timedelta(seconds=lease_seconds)
        for row in rows:
            row.status = "sending"
            row.attempts += 1
            row.next_retry_at = lease_deadline
            claimed.append(
                _ClaimedRow(
                    id=row.id,
                    notification_id=row.notification_id,
                    tenant_id=row.tenant_id,
                    channel=row.channel,
                    recipient=row.recipient,
                    template_key=row.template_key,
                    locale=row.locale,
                    params=dict(row.params),
                    attempts=row.attempts,
                )
            )
    return claimed


async def _process_row(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    bus: EventBus,
    settings: Settings,
    cipher: SecretCipher,
    registry: TemplateRegistry,
    channels: dict[str, NotificationChannel],
    cap: SmsDailyCap,
    row: _ClaimedRow,
) -> None:
    try:
        rendered = registry.render(row.template_key, row.locale, row.params)
    except NotFoundError as exc:
        await _dead(maintenance_sessions, bus, row, f"unknown template: {exc}")
        return

    channel = await _resolve_channel(maintenance_sessions, cipher, channels, row)

    if (
        row.channel == _SMS_CHANNEL
        and channel.configured
        and not await cap.try_consume(row.tenant_id)
    ):
        await _dead(maintenance_sessions, bus, row, "sms_daily_cap_exceeded")
        return

    message = RenderedMessage(
        notification_id=row.notification_id,
        address=row.recipient,
        subject=rendered.subject,
        body=rendered.body,
        locale=row.locale,
    )
    try:
        result = await channel.send(message)
    except ChannelPermanentError as exc:
        await _dead(maintenance_sessions, bus, row, f"permanent: {exc}")
    except Exception as exc:  # temporary / circuit-open / unexpected -> retry or dead
        if row.attempts >= settings.notification_max_attempts:
            await _dead(maintenance_sessions, bus, row, f"exhausted: {exc}")
        else:
            await _reschedule(maintenance_sessions, row, str(exc))
    else:
        await _mark_sent(maintenance_sessions, row, result.provider_message_id)


async def _resolve_channel(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
    channels: dict[str, NotificationChannel],
    row: _ClaimedRow,
) -> NotificationChannel:
    """Per-tenant config overrides the platform channel; else the platform adapter."""
    if row.tenant_id is not None:
        async with SqlAlchemyUnitOfWork(maintenance_sessions, context=_system_ctx()) as uow:
            setting = (
                await uow.session.execute(
                    select(NotificationSetting).where(
                        NotificationSetting.tenant_id == row.tenant_id,
                        NotificationSetting.channel == row.channel,
                        NotificationSetting.is_enabled.is_(True),
                    )
                )
            ).scalar_one_or_none()
            config_blob = bytes(setting.config_encrypted) if setting is not None else None
        if config_blob is not None:
            config = json.loads(cipher.decrypt(config_blob))
            return build_channel_from_config(row.channel, config)
    return channels[row.channel]


async def _update_row(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    row_id: UUID,
    apply: Any,
) -> None:
    async with SqlAlchemyUnitOfWork(maintenance_sessions, context=_system_ctx()) as uow:
        row = await uow.session.get(NotificationOutbox, row_id)
        if row is not None:
            apply(row)


async def _mark_sent(
    maintenance_sessions: async_sessionmaker[AsyncSession], row: _ClaimedRow, message_id: str | None
) -> None:
    def apply(entity: NotificationOutbox) -> None:
        entity.status = "sent"
        entity.sent_at = datetime.now(UTC)
        entity.provider_message_id = message_id
        entity.last_error = None

    await _update_row(maintenance_sessions, row.id, apply)


async def _reschedule(
    maintenance_sessions: async_sessionmaker[AsyncSession], row: _ClaimedRow, error: str
) -> None:
    delay = min(_BACKOFF_BASE_SECONDS * 2 ** (row.attempts - 1), _BACKOFF_CAP_SECONDS)
    next_retry = datetime.now(UTC) + timedelta(seconds=delay)

    def apply(entity: NotificationOutbox) -> None:
        entity.status = "failed"
        entity.next_retry_at = next_retry
        entity.last_error = error[:500]

    await _update_row(maintenance_sessions, row.id, apply)


async def _dead(
    maintenance_sessions: async_sessionmaker[AsyncSession],
    bus: EventBus,
    row: _ClaimedRow,
    error: str,
) -> None:
    def apply(entity: NotificationOutbox) -> None:
        entity.status = "dead"
        entity.last_error = error[:500]

    await _update_row(maintenance_sessions, row.id, apply)
    await bus.publish(
        EventEnvelope(
            event_id=new_uuid7(),
            name="notifications.message.failed",
            version=1,
            occurred_at=datetime.now(UTC),
            tenant_id=row.tenant_id,
            actor=Actor(kind="system", id="notifications.dispatcher"),
            payload={
                "notification_id": str(row.notification_id),
                "channel": row.channel,
                "recipient": mask_address(row.recipient),
                "template_key": row.template_key,
                "error": error[:200],
            },
        )
    )
