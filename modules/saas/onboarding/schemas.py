"""Public DTOs of saas.onboarding (Pydantic frozen)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OnboardingStepDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    completed: bool
    completed_at: datetime | None


class OnboardingProgressDTO(BaseModel):
    """The tenant's checklist: one entry per configured step (in configured order)
    plus roll-up counts. ``is_complete`` is True only when there is at least one
    step and every configured step is done."""

    model_config = ConfigDict(frozen=True)

    steps: list[OnboardingStepDTO]
    completed_count: int
    total: int
    is_complete: bool
