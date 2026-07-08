"""Tenant-scoped repository for the active-plan snapshot (one row per tenant)."""

from modules.saas.entitlements.models import TenantEntitlement
from shared.repository import Repository


class TenantEntitlementRepository(Repository[TenantEntitlement]):
    model = TenantEntitlement
