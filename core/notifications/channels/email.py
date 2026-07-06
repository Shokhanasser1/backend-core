"""Email/SMTP channel (interfaces §4.2).

Dormant when no SMTP host. Uses stdlib smtplib on a worker thread (no async-SMTP
dependency); STARTTLS + optional auth. Recipient/sender refusals are permanent
(dead-letter); connection/transient SMTP errors are temporary (retry). A
per-channel circuit breaker fails fast when the relay is down.
"""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import ClassVar

from core.notifications.channel_config import mask_address
from core.notifications.ports import (
    ChannelPermanentError,
    ChannelResult,
    ChannelTemporaryError,
    RenderedMessage,
)
from shared.resilience import CircuitBreaker, RetryPolicy, call_resilient

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


class EmailChannel:
    code: ClassVar[str] = "email"

    def __init__(
        self,
        *,
        host: str | None,
        port: int,
        username: str | None,
        password: str | None,
        from_address: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from = from_address
        self._breaker = CircuitBreaker(name="email", failure_threshold=5, recovery_time=60.0)

    @property
    def configured(self) -> bool:
        return bool(self._host)

    async def send(self, message: RenderedMessage) -> ChannelResult:
        if not self.configured:
            logger.info("email dormant; skipping", extra={"to": mask_address(message.address)})
            return ChannelResult(provider_message_id=None)

        email_message = EmailMessage()
        email_message["From"] = self._from
        email_message["To"] = message.address
        email_message["Subject"] = message.subject or ""
        email_message.set_content(message.body)

        async def _op() -> None:
            await asyncio.to_thread(self._deliver, email_message)

        await call_resilient(
            _op,
            timeout=_TIMEOUT,
            retry=RetryPolicy(attempts=2),
            breaker=self._breaker,
            error_cls=ChannelTemporaryError,
        )
        logger.info("email sent", extra={"to": mask_address(message.address)})
        return ChannelResult(provider_message_id=None)

    def _deliver(self, email_message: EmailMessage) -> None:
        assert self._host is not None  # noqa: S101 - guarded by ``configured``
        try:
            with smtplib.SMTP(self._host, self._port, timeout=_TIMEOUT) as server:
                server.starttls(context=ssl.create_default_context())
                if self._username and self._password:
                    server.login(self._username, self._password)
                server.send_message(email_message)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            raise ChannelPermanentError(f"smtp refused address: {exc}") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise ChannelTemporaryError(f"smtp error: {exc}") from exc
