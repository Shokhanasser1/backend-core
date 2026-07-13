"""Request-scoped OnboardingService (built on the authenticated ServiceBundle).

The configured step list comes from app.state.settings (SAAS_ONBOARDING_STEPS), so
the feature never imports app; the bus comes from app.state too. The permission
check lives on the route (require_permission); this only assembles the service.
"""

from fastapi import Depends, Request

from core.auth.deps import ServiceBundle, authed_bundle
from modules.saas.onboarding.service import OnboardingService


async def onboarding_service(
    request: Request, bundle: ServiceBundle = Depends(authed_bundle)
) -> OnboardingService:
    return OnboardingService(
        bundle.uow,
        request.app.state.bus,
        bundle.ctx,
        steps=request.app.state.settings.saas_onboarding_step_list,
    )
