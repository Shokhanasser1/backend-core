"""Telegram Bot API channel (interfaces §4.2).

Dormant when no bot token. sendMessage over HTTPS; 5xx/429 -> temporary (retry),
a Bot API ``ok:false`` (bad chat_id, blocked) -> permanent (dead-letter). Network
errors are normalised to temporary. A per-channel circuit breaker fails fast when
Telegram is down. Recipient is masked in logs.
"""

import logging
from typing import ClassVar

import httpx

from core.notifications.channel_config import mask_address
from core.notifications.ports import (
    ChannelPermanentError,
    ChannelResult,
    ChannelTemporaryError,
    RenderedMessage,
)
from shared.resilience import CircuitBreaker, RetryPolicy, call_resilient

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_TIMEOUT = 15.0


class TelegramChannel:
    code: ClassVar[str] = "telegram"

    def __init__(
        self, bot_token: str | None, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._bot_token = bot_token
        self._transport = transport  # tests inject httpx.MockTransport
        self._breaker = CircuitBreaker(name="telegram", failure_threshold=5, recovery_time=60.0)

    @property
    def configured(self) -> bool:
        return bool(self._bot_token)

    async def send(self, message: RenderedMessage) -> ChannelResult:
        if not self.configured:
            logger.info("telegram dormant; skipping", extra={"to": mask_address(message.address)})
            return ChannelResult(provider_message_id=None)

        async def _op() -> str:
            try:
                async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT) as client:
                    response = await client.post(
                        f"{_API_BASE}/bot{self._bot_token}/sendMessage",
                        json={"chat_id": message.address, "text": message.body},
                    )
            except httpx.HTTPError as exc:
                raise ChannelTemporaryError(f"telegram transport error: {exc}") from exc
            if response.status_code >= 500 or response.status_code == 429:
                raise ChannelTemporaryError(f"telegram http {response.status_code}")
            data = response.json()
            if not data.get("ok"):
                raise ChannelPermanentError(f"telegram rejected: {data.get('description')}")
            return str(data["result"]["message_id"])

        message_id = await call_resilient(
            _op,
            timeout=_TIMEOUT,
            retry=RetryPolicy(attempts=2),
            breaker=self._breaker,
            error_cls=ChannelTemporaryError,
        )
        logger.info(
            "telegram sent", extra={"to": mask_address(message.address), "message_id": message_id}
        )
        return ChannelResult(provider_message_id=message_id)
