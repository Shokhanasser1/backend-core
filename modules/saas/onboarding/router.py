"""Onboarding checklist endpoints (/api/saas/onboarding). Every route carries one
permission marker (§5.2). Steps are also completable server-side via
OnboardingService.complete_step (glue / sibling features at a milestone).
"""

from fastapi import APIRouter, Depends

from core.auth.deps import require_permission
from modules.saas.onboarding import permissions as perms
from modules.saas.onboarding.deps import onboarding_service
from modules.saas.onboarding.schemas import OnboardingProgressDTO
from modules.saas.onboarding.service import OnboardingService

router = APIRouter(prefix="/api/saas/onboarding", tags=["saas.onboarding"])


@router.get("/me", dependencies=[Depends(require_permission(perms.ONBOARDING_READ))])
async def my_progress(
    service: OnboardingService = Depends(onboarding_service),
) -> OnboardingProgressDTO:
    """The current tenant's onboarding checklist."""
    return await service.progress()


@router.post(
    "/steps/{step_key}/complete",
    dependencies=[Depends(require_permission(perms.ONBOARDING_UPDATE))],
)
async def complete_step(
    step_key: str, service: OnboardingService = Depends(onboarding_service)
) -> OnboardingProgressDTO:
    """Mark a configured step done (idempotent); returns the updated checklist."""
    return await service.complete_step(step_key)
