"""OnboardingService — the public interface of saas.onboarding (§1.2).

A tenant activation checklist. The set of steps is configuration
(``steps``, from SAAS_ONBOARDING_STEPS) so a client defines its own journey; this
service records which of them a tenant has completed and reports progress.

Steps are marked done EXPLICITLY (owner decision): server-side glue / sibling
features call ``complete_step`` at the milestone, and an authenticated route lets
the frontend mark user-driven steps. Onboarding does not subscribe to the bus
(features may not use wildcards — §1.1 — and a generic checklist must not hardwire
other modules' event names). ``saas.onboarding.completed`` is emitted once, when
the final configured step is completed.
"""

from datetime import UTC, datetime

from modules.saas.onboarding.models import OnboardingProgress
from modules.saas.onboarding.repository import OnboardingRepository
from modules.saas.onboarding.schemas import OnboardingProgressDTO, OnboardingStepDTO
from shared.context import TenantContext
from shared.errors import InvariantViolationError
from shared.events import EventBus
from shared.service import Service, UnitOfWork


class OnboardingService(Service):
    def __init__(
        self, uow: UnitOfWork, bus: EventBus, ctx: TenantContext, *, steps: tuple[str, ...]
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._repo = OnboardingRepository(uow.session, ctx)
        self._steps = steps

    async def complete_step(self, step_key: str) -> OnboardingProgressDTO:
        """Mark ``step_key`` done (idempotent). Rejects a step outside the configured
        checklist. Emits ``saas.onboarding.completed`` exactly when this call finishes
        the last remaining step."""
        if step_key not in self._steps:
            raise InvariantViolationError(f"unknown onboarding step: {step_key!r}")
        completed = await self._completed()
        if step_key in completed:
            return self._to_dto(completed)  # already done — idempotent, no re-emit
        completed[step_key] = datetime.now(UTC)
        await self._repo.add(
            OnboardingProgress(step_key=step_key, completed_at=completed[step_key])
        )
        if self._steps and set(completed).issuperset(self._steps):
            self.emit("saas.onboarding.completed", {})
        return self._to_dto(completed)

    async def progress(self) -> OnboardingProgressDTO:
        return self._to_dto(await self._completed())

    async def _completed(self) -> dict[str, datetime]:
        rows = await self._repo.find(page=None)
        return {row.step_key: row.completed_at for row in rows}

    def _to_dto(self, completed: dict[str, datetime]) -> OnboardingProgressDTO:
        steps = [
            OnboardingStepDTO(
                key=step, completed=step in completed, completed_at=completed.get(step)
            )
            for step in self._steps
        ]
        done = sum(1 for step in steps if step.completed)
        return OnboardingProgressDTO(
            steps=steps,
            completed_count=done,
            total=len(self._steps),
            is_complete=bool(self._steps) and done == len(self._steps),
        )
