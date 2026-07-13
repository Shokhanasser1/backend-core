"""Tenant-scoped repository for CRM deals."""

from modules.crm.deals.models import Deal
from shared.repository import Repository


class DealRepository(Repository[Deal]):
    model = Deal
