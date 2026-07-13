"""Tenant-scoped repository for onboarding progress rows."""

from modules.saas.onboarding.models import OnboardingProgress
from shared.repository import Repository


class OnboardingRepository(Repository[OnboardingProgress]):
    model = OnboardingProgress
