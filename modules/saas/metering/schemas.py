"""Public DTOs of saas.metering (Pydantic frozen)."""

from datetime import date

from pydantic import BaseModel, ConfigDict


class UsageWindowDTO(BaseModel):
    """Per-metric totals over an optional [since, until] day window (inclusive).
    ``since``/``until`` are None when unbounded (all recorded history)."""

    model_config = ConfigDict(frozen=True)

    since: date | None
    until: date | None
    metrics: dict[str, int]
