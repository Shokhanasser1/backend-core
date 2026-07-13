"""saas.onboarding — tenant activation checklist (configurable steps).

The loader (app/features.py) treats a feature package as two optional hooks:
``install()`` (registers RBAC at startup) and ``router`` (an APIRouter it mounts).
Everything else is internal to the feature; only OnboardingService (and its DTOs)
is a public interface (§1.2) that callers import from here (the package). No bus
subscribers: steps are completed through explicit ``complete_step`` calls (owner
decision), so a generic checklist never hardwires other modules' event names.
"""

from modules.saas.onboarding.permissions import register_saas_onboarding_rbac
from modules.saas.onboarding.router import router
from modules.saas.onboarding.schemas import OnboardingProgressDTO, OnboardingStepDTO
from modules.saas.onboarding.service import OnboardingService

__all__ = [
    "OnboardingProgressDTO",
    "OnboardingService",
    "OnboardingStepDTO",
    "install",
    "router",
]


def install() -> None:
    """Startup wiring for the feature (called by the loader when saas is enabled)."""
    register_saas_onboarding_rbac()
