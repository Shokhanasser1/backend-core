"""Tenant context and actor (interfaces doc §2.1)."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

ActorKind = Literal["user", "system", "integration"]


@dataclass(frozen=True, slots=True)
class Actor:
    kind: ActorKind
    id: str | None  # user_id | worker/job name | provider code ("payme")


@dataclass(frozen=True, slots=True)
class RequestContext:
    ip: str | None
    user_agent: str | None  # request_id is not duplicated here — it lives in TenantContext


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Ambient context of a unit of work.

    ``tenant_id is None`` is legal in exactly two cases: user-scoped requests
    (authenticated but outside a tenant) and system paths (read-only until the
    target object is identified; writes require context elevation — §2.1).
    """

    tenant_id: UUID | None
    actor: Actor
    request_id: str | None
    locale: str = "ru"


SYSTEM_ACTOR = Actor(kind="system", id=None)
