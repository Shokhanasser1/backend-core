"""Public DTOs + recipient inputs of core/notifications (interfaces §3.4).

Recipients are frozen dataclasses (inputs to send); status/config views are
Pydantic frozen DTOs. Secret channel config never appears in any DTO
(write-only contract, threat model V10).
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# --- recipient inputs ---


@dataclass(frozen=True, slots=True)
class UserRecipient:
    """Addresses (email/phone) and locale come from the user's profile."""

    user_id: UUID


@dataclass(frozen=True, slots=True)
class AddressRecipient:
    """A concrete address for one channel: chat_id | E.164 | email."""

    channel: str
    address: str


Recipient = UserRecipient | AddressRecipient

NotificationStatus = Literal["queued", "sent", "partially_failed", "failed"]


# --- output DTOs ---


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class NotificationStatusDTO(_Frozen):
    notification_id: UUID
    status: NotificationStatus


class ChannelStatusDTO(_Frozen):
    channel: str
    configured: bool
    is_enabled: bool
