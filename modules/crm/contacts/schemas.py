"""Public DTOs of crm.contacts (Pydantic frozen).

PATCH convention (same as commerce.products across this template): on the Update
inputs, a field left unset (``None``) means "leave unchanged" — clearing a field
back to null is not expressed in v1. Reassigning a contact to a different company
works; un-assigning happens by deleting the company (FK SET NULL).
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CompanyDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    name: str
    website: str | None
    industry: str | None
    notes: str | None


class ContactDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    company_id: UUID | None
    first_name: str
    last_name: str | None
    email: str | None
    phone: str | None
    position: str | None
    notes: str | None


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCompanyIn(_StrictIn):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    industry: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class UpdateCompanyIn(_StrictIn):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    industry: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class CreateContactIn(_StrictIn):
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    position: str | None = Field(default=None, max_length=120)
    company_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class UpdateContactIn(_StrictIn):
    first_name: str | None = Field(default=None, min_length=1, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    position: str | None = Field(default=None, max_length=120)
    company_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)
