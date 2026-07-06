"""NotificationChannel port + DTOs (interfaces §4.2).

Implementations (Telegram, Eskiz SMS, email/SMTP) live in channels/. The
dispatcher renders a template into a RenderedMessage and hands it to the channel.

Error taxonomy drives the dispatcher's retry decision:
- ChannelTemporaryError IS an ExternalServiceError (transient) -> retried by
  call_resilient inside the adapter and, if still failing, rescheduled with
  backoff by the dispatcher;
- ChannelPermanentError is deliberately NOT an ExternalServiceError -> never
  retried (bad address / undeliverable) -> the outbox row goes straight to dead.
"""

from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import UUID

from shared.errors import DomainError, NotificationChannelError


class ChannelTemporaryError(NotificationChannelError):
    """Transient channel failure (5xx, timeout, rate limit) -> retry."""


class ChannelPermanentError(DomainError):
    """Undeliverable (invalid address, rejected content) -> no retry, dead-letter."""

    code = "notification_undeliverable"
    message_key = "errors.notification_channel_error"
    http_status = 422


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    notification_id: UUID
    address: str  # chat_id | E.164 | email
    subject: str | None  # email only
    body: str
    locale: str


@dataclass(frozen=True, slots=True)
class ChannelResult:
    provider_message_id: str | None


class NotificationChannel(Protocol):
    code: ClassVar[str]

    @property
    def configured(self) -> bool:
        """False when the channel has no credentials (dormant): send() is a
        logged no-op, never an error (a fresh template checkout has no creds)."""
        ...

    async def send(self, message: RenderedMessage) -> ChannelResult: ...
