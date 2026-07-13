"""Public DTOs of crm.deals (Pydantic frozen).

PATCH convention (template-wide, as in commerce.products / crm.contacts): a field
left unset (``None``) means "leave unchanged". The deal keeps ids of the linked
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
