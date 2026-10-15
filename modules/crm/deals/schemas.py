"""Public DTOs of crm.deals (Pydantic frozen).

PATCH convention (template-wide, as in commerce.products / crm.contacts): a field
left unset (``None``) means "leave unchanged". Exception: ``contact_id`` /
``company_id`` sent as an explicit JSON ``null`` clear the link. The deal keeps ids of the linked
contact/company (not their details) — a caller fetches those through ContactsService.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DealDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    title: str
    stage: str
    amount: int  # minor units of `currency`
    currency: str
    contact_id: UUID | None
    company_id: UUID | None
    notes: str | None


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateDealIn(_StrictIn):
    title: str = Field(min_length=1, max_length=200)
    amount: int = Field(ge=0)
    currency: str = Field(default="UZS", min_length=3, max_length=3)
    # None -> lands on the first configured stage (service decides).
    stage: str | None = Field(default=None, min_length=1, max_length=64)
    contact_id: UUID | None = None
    company_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class UpdateDealIn(_StrictIn):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    amount: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    stage: str | None = Field(default=None, min_length=1, max_length=64)
    contact_id: UUID | None = None
    company_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class DealChanges(BaseModel):
    """A PATCH to apply (service input): ``None`` = leave unchanged; the
    ``clear_*`` flags remove a link (an explicit JSON ``null`` on the route)."""

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    amount: int | None = None
    currency: str | None = None
    stage: str | None = None
    contact_id: UUID | None = None
    company_id: UUID | None = None
    notes: str | None = None
    clear_contact: bool = False
    clear_company: bool = False

    @classmethod
    def from_request(cls, body: "UpdateDealIn") -> "DealChanges":
        sent = body.model_fields_set
        return cls(
            **body.model_dump(),
            clear_contact="contact_id" in sent and body.contact_id is None,
            clear_company="company_id" in sent and body.company_id is None,
        )
