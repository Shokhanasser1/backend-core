"""crm.activities — calls, meetings, tasks and emails: due date, responsible member,
links to a contact/company (crm.contacts) and a deal (crm.deals).

The loader (app/features.py) treats a feature package as two optional hooks:
``install()`` (registers RBAC + bus subscribers at startup) and ``router`` (an
APIRouter it mounts). Only ActivityService and its DTOs are a public interface
(§1.2). Depends on crm.contacts and crm.deals (``requires_features``):
links are validated through their public services, never their tables, and are
cleared when those features publish a deletion.
"""

from modules.crm.activities.permissions import register_crm_activities_rbac
from modules.crm.activities.router import router
from modules.crm.activities.schemas import (
    ActivityChanges,
    ActivityDTO,
    ActivityQuery,
    CreateActivityIn,
)
from modules.crm.activities.service import ActivityService

__all__ = [
    "ActivityChanges",
    "ActivityDTO",
    "ActivityQuery",
    "ActivityService",
    "CreateActivityIn",
    "install",
    "router",
]


def install() -> None:
    """Startup wiring for the feature (called by the loader when crm is enabled)."""
    register_crm_activities_rbac()
    import modules.crm.activities.subscribers  # noqa: F401  (bus subscribers: unlink on delete)
