"""Public DTOs of saas.entitlements (Pydantic frozen)."""

from pydantic import BaseModel, ConfigDict


class EntitlementsDTO(BaseModel):
    """The tenant's effective entitlements — what a UI wizard / client reads.

    ``plan_code`` is None when the tenant has no active plan (no subscription, or
    a canceled one past its period end): flags then read as disabled and limits
    as unlimited (see EntitlementService).
    """

    model_config = ConfigDict(frozen=True)

    plan_code: str | None
    flags: dict[str, bool]
    limits: dict[str, int | None]  # None value = unlimited
