"""Audit query + public DTO (interfaces §3.5).

``AuditQuery`` is the service-layer filter for the admin activity log; every
field is optional and narrows the search. ``AuditRecordDTO`` is the API boundary
shape returned to the admin screen.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True, slots=True)
class AuditQuery:
    action_prefix: str | None = None  # e.g. "auth." or "billing.payment."
    actor_user_id: UUID | None = None
    object_type: str | None = None
    object_id: str | None = None
    date_from: datetime | None = None  # inclusive lower bound on created_at
    date_to: datetime | None = None  # exclusive upper bound on created_at


class AuditRecordDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    user_id: UUID | None
    request_id: str | None
    action: str
    object_type: str | None
    object_id: str | None
    ip: str | None
    user_agent: str | None
    payload: dict[str, Any]
    created_at: datetime
