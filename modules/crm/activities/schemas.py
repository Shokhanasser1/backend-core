"""Public DTOs of crm.activities (Pydantic).

``CreateActivityIn`` is both the request body and the service input (a sibling or
glue code builds it directly). PATCH convention (template-wide): a field left unset
(``None``) means "leave unchanged"; an explicit JSON ``null`` on ``contact_id``,
``company_id``, ``deal_id`` or ``due_at`` clears it (``ActivityChanges.cleared``).
"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Fields an explicit JSON null clears (the rest treat null as "unchanged").
CLEARABLE = frozenset({"contact_id", "company_id", "deal_id", "due_at"})


class ActivityDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    type: str
    subject: str
    notes: str | None
    due_at: AwareDatetime | None
    done_at: AwareDatetime | None
    assignee_user_id: UUID
    contact_id: UUID | None
    company_id: UUID | None
    deal_id: UUID | None


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateActivityIn(_StrictIn):
    subject: str = Field(min_length=1, max_length=200)
    # None -> the first configured type (service decides).
    type: str | None = Field(default=None, min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)
    due_at: AwareDatetime | None = None
    # None -> the creator.
    assignee_user_id: UUID | None = None
    contact_id: UUID | None = None
    company_id: UUID | None = None
    deal_id: UUID | None = None


class UpdateActivityIn(_StrictIn):
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    type: str | None = Field(default=None, min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)
    due_at: AwareDatetime | None = None
    assignee_user_id: UUID | None = None
    contact_id: UUID | None = None
    company_id: UUID | None = None
    deal_id: UUID | None = None


class ActivityChanges(BaseModel):
    """A PATCH to apply (service input): ``None`` = leave unchanged; names in
    ``cleared`` are set to NULL (an explicit JSON ``null`` on the route)."""

    model_config = ConfigDict(frozen=True)

    subject: str | None = None
    type: str | None = None
    notes: str | None = None
    due_at: AwareDatetime | None = None
    assignee_user_id: UUID | None = None
    contact_id: UUID | None = None
    company_id: UUID | None = None
    deal_id: UUID | None = None
    cleared: frozenset[str] = frozenset()

    @classmethod
    def from_request(cls, body: UpdateActivityIn) -> "ActivityChanges":
        sent_null = {name for name in body.model_fields_set if getattr(body, name) is None}
        return cls(**body.model_dump(), cleared=frozenset(sent_null & CLEARABLE))


class ActivityQuery(BaseModel):
    """List filters (query parameters). ``mine`` = assigned to the caller;
    ``overdue`` = open with ``due_at`` in the past; ``due_today`` = ``due_at`` within
    the current UTC day."""

    model_config = ConfigDict(extra="forbid")

    assignee_user_id: UUID | None = None
    mine: bool = False
    status: Literal["open", "done"] | None = None
    overdue: bool = False
    due_today: bool = False
    type: str | None = Field(default=None, max_length=64)
    deal_id: UUID | None = None
    contact_id: UUID | None = None
    company_id: UUID | None = None
