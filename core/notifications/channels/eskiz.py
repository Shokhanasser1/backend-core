"""Eskiz SMS channel (interfaces §4.2; reference: IELTS-Simulator sms.py).

Dormant when no credentials. Bearer token fetched lazily and cached in-instance
(Eskiz tokens live ~24h); a 401 on send transparently re-authenticates and retries
ONCE. Phone numbers are normalised to 998XXXXXXXXX; a number that cannot be
normalised is a permanent failure. Body is capped at 280 chars. Eskiz moderates
sender templates — production texts must be pre-approved with Eskiz.
"""

import logging
import re
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

_API_BASE = "https://notify.eskiz.uz/api"
_TIMEOUT = 15.0
_MAX_SMS_LEN = 280
_DEFAULT_SENDER = "4546"  # Eskiz test sender; production sender is moderated


def normalize_uz_phone(raw: str) -> str:
    """+998 90 123-45-67 | 998901234567 | 901234567 -> 998901234567."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9:
        digits = f"998{digits}"
    if len(digits) == 12 and digits.startswith("998"):
        return digits
    raise ChannelPermanentError(f"not a valid UZ phone number: {mask_address(raw)}")


class EskizSmsChannel:
    code: ClassVar[str] = "sms_eskiz"

    def __init__(
        self,
        email: str | None,
        password: str | None,
        *,
        sender: str = _DEFAULT_SENDER,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._sender = sender
        self._transport = transport
        self._token: str | None = None
        self._breaker = CircuitBreaker(name="sms_eskiz", failure_threshold=5, recovery_time=60.0)

    @property
    def configured(self) -> bool:
        return bool(self._email and self._password)

    async def send(self, message: RenderedMessage) -> ChannelResult:
        if not self.configured:
            logger.info("eskiz dormant; skipping", extra={"to": mask_address(message.address)})
            return ChannelResult(provider_message_id=None)
        phone = normalize_uz_phone(message.address)  # ChannelPermanentError on bad number
        body = message.body[:_MAX_SMS_LEN]

        async def _op() -> str:
            try:
                async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT) as client:
                    response = await self._post_sms(client, phone, body)
                    if response.status_code == 401:  # token expired -> re-auth once
                        self._token = None
                        response = await self._post_sms(client, phone, body)
            except httpx.HTTPError as exc:
                raise ChannelTemporaryError(f"eskiz transport error: {exc}") from exc
            return self._parse_send(response)

        message_id = await call_resilient(
            _op,
            timeout=_TIMEOUT,
            retry=RetryPolicy(attempts=2),
            breaker=self._breaker,
            error_cls=ChannelTemporaryError,
        )
        logger.info(
            "eskiz sent", extra={"to": mask_address(message.address), "message_id": message_id}
        )
        return ChannelResult(provider_message_id=message_id)

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        if self._token is not None:
            return self._token
        response = await client.post(
            f"{_API_BASE}/auth/login",
            data={"email": self._email, "password": self._password},
        )
        if response.status_code >= 500 or response.status_code == 429:
            raise ChannelTemporaryError(f"eskiz auth http {response.status_code}")
        if response.status_code >= 400:
            raise ChannelPermanentError(f"eskiz auth rejected: http {response.status_code}")
        token = response.json().get("data", {}).get("token")
        if not token:
            raise ChannelTemporaryError("eskiz auth returned no token")
        self._token = str(token)
        return self._token

    async def _post_sms(self, client: httpx.AsyncClient, phone: str, body: str) -> httpx.Response:
        token = await self._ensure_token(client)
        return await client.post(
            f"{_API_BASE}/message/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            data={"mobile_phone": phone, "message": body, "from": self._sender},
        )

    def _parse_send(self, response: httpx.Response) -> str:
        if response.status_code >= 500 or response.status_code == 429:
            raise ChannelTemporaryError(f"eskiz http {response.status_code}")
        if response.status_code >= 400:
            raise ChannelPermanentError(f"eskiz rejected: http {response.status_code}")
        data = response.json()
        if str(data.get("status")) == "error":
            raise ChannelPermanentError(f"eskiz error: {data.get('message')}")
        return str(data.get("id") or "")
