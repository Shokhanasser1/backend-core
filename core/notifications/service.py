"""NotificationService (interfaces §3.4): queue sends into the outbox, report
status, manage per-tenant channel config (write-only).

send() does not render — it resolves addresses + locale and writes one outbox row
per channel under a shared notification_id; the arq dispatcher (Phase 3, Task 16)
renders template_key + params + locale at delivery time. Locale chain:
explicit -> user profile -> ambient ctx -> 'ru'. Idempotent by dedup_key: a repeat
returns the existing notification_id without inserting.
"""

import json
from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from core.auth.directory import UserDirectory
from core.notifications.channel_config import (
    KNOWN_CHANNELS,
    require_known_channel,
    validate_channel_config,
)
from core.notifications.models import NotificationOutbox, NotificationSetting
from core.notifications.registry import TemplateRegistry
from core.notifications.repository import (
    NotificationOutboxRepository,
    NotificationSettingRepository,
)
from core.notifications.schemas import (
    AddressRecipient,
    ChannelStatusDTO,
    NotificationStatus,
    NotificationStatusDTO,
    Recipient,
    UserRecipient,
)
from shared.context import TenantContext
from shared.encryption import SecretCipher
from shared.errors import InvariantViolationError, NotFoundError
from shared.events import EventBus
from shared.i18n import negotiate_locale
from shared.ids import new_uuid7
from shared.service import Service, UnitOfWork

_NONTERMINAL = frozenset({"pending", "sending", "failed"})
# Channels addressable from a bare user profile (no stored telegram chat_id in v1).
_USER_PROFILE_CHANNELS = ("email", "sms_eskiz")

# JSON-safe primitives; anything else in params is stringified (jsonb, no secrets).
_JSON_PRIMITIVES = (str, int, float, bool, type(None))


def _jsonable(params: Mapping[str, object]) -> dict[str, object]:
    return {k: (v if isinstance(v, _JSON_PRIMITIVES) else str(v)) for k, v in params.items()}


def _aggregate_status(rows: Sequence[NotificationOutbox]) -> NotificationStatus:
    statuses = {row.status for row in rows}
    if statuses & _NONTERMINAL:
        return "queued"
    if statuses == {"sent"}:
        return "sent"
    if statuses == {"dead"}:
        return "failed"
    return "partially_failed"  # terminal mix of sent + dead


class NotificationService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        registry: TemplateRegistry,
        cipher: SecretCipher,
        directory: UserDirectory | None = None,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._registry = registry
        self._cipher = cipher
        self._directory = directory
        self._outbox = NotificationOutboxRepository(uow.session, ctx)

    async def send(
        self,
        recipient: Recipient,
        template: str,
        context: Mapping[str, object],
        *,
        channels: Sequence[str] | None = None,
        locale: str | None = None,
        dedup_key: str | None = None,
    ) -> UUID:
        tdef = self._registry.get(template)  # NotFoundError on unknown template
        selected = tuple(channels) if channels is not None else tdef.default_channels
        unknown = set(selected) - KNOWN_CHANNELS
        if unknown:
            raise NotFoundError(f"unknown channel(s): {sorted(unknown)}")
        missing = tdef.required_context - set(context)
        if missing:
            raise InvariantViolationError(f"missing required context: {sorted(missing)}")

        addresses, user_locale = await self._resolve_addresses(recipient, selected)
        if not addresses:
            raise InvariantViolationError("recipient has no address for any requested channel")
        resolved_locale = negotiate_locale(locale, user_locale, self._ctx.locale)

        if dedup_key is not None:
            existing = await self._outbox.notification_id_for_dedup(dedup_key)
            if existing is not None:
                return existing

        notification_id = new_uuid7()
        params = _jsonable(context)
        rows = [
            NotificationOutbox(
                id=new_uuid7(),
                notification_id=notification_id,
                dedup_key=dedup_key,
                tenant_id=self._ctx.tenant_id,
                channel=channel,
                recipient=address,
                template_key=template,
                locale=resolved_locale,
                params=params,
            )
            for channel, address in addresses.items()
        ]
        try:
            await self._outbox.add_all(rows)
        except IntegrityError:
            # Concurrent send with the same dedup_key won the unique index; reuse it.
            if dedup_key is not None:
                existing = await self._outbox.notification_id_for_dedup(dedup_key)
                if existing is not None:
                    return existing
            raise
        return notification_id

    async def _resolve_addresses(
        self, recipient: Recipient, channels: Sequence[str]
    ) -> tuple[dict[str, str], str | None]:
        if isinstance(recipient, AddressRecipient):
            addresses = (
                {recipient.channel: recipient.address} if recipient.channel in channels else {}
            )
            return addresses, None
        if not isinstance(recipient, UserRecipient):  # exhaustive guard
            raise InvariantViolationError("unsupported recipient type")
        if self._directory is None:
            raise InvariantViolationError("user recipient requires a user directory")
        contact = await self._directory.get_contact(recipient.user_id)
        if contact is None:
            raise NotFoundError(f"recipient user not found: {recipient.user_id}")
        by_channel: dict[str, str | None] = {
            "email": contact.email,
            "sms_eskiz": contact.phone,
        }
        resolved: dict[str, str] = {}
        for channel in channels:
            if channel not in _USER_PROFILE_CHANNELS:
                continue
            address = by_channel.get(channel)
            if address:
                resolved[channel] = address
        return resolved, contact.locale

    async def get_status(self, notification_id: UUID) -> NotificationStatusDTO:
        rows = await self._outbox.rows_for_notification(notification_id)
        if not rows:
            raise NotFoundError(f"notification not found: {notification_id}")
        return NotificationStatusDTO(
            notification_id=notification_id, status=_aggregate_status(rows)
        )

    # --- per-tenant channel config (write-only; threat model V10) ---

    async def set_channel_config(self, channel: str, config: Mapping[str, object]) -> None:
        validate_channel_config(channel, config)
        settings = NotificationSettingRepository(self._session, self._ctx)
        encrypted = self._cipher.encrypt(json.dumps(dict(config), sort_keys=True))
        existing = await settings.get_by_channel(channel)
        if existing is not None:
            existing.config_encrypted = encrypted
            await self._session.flush()
            return
        await settings.add(
            NotificationSetting(id=new_uuid7(), channel=channel, config_encrypted=encrypted)
        )

    async def get_channel_status(self, channel: str) -> ChannelStatusDTO:
        require_known_channel(channel)
        settings = NotificationSettingRepository(self._session, self._ctx)
        existing = await settings.get_by_channel(channel)
        return ChannelStatusDTO(
            channel=channel,
            configured=existing is not None,
            is_enabled=existing.is_enabled if existing is not None else False,
        )
