"""Tenant-scoped repository for CRM activities."""

from modules.crm.activities.models import Activity
from shared.repository import Repository


class ActivityRepository(Repository[Activity]):
    model = Activity
