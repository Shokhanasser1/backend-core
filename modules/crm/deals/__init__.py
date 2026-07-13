"""crm.deals — sales opportunities moving through a configurable funnel.

The loader (app/features.py) treats a feature package as two optional hooks:
``install()`` (registers RBAC at startup) and ``router`` (an APIRouter it mounts).
Only DealService (and its DTO) is a public interface (§1.2). Depends on crm.contacts
(``requires_features``): a deal validates its contact/company links through the
public ContactsService, never reading that feature's tables. No bus subscribers.
"""

from modules.crm.deals.permissions import register_crm_deals_rbac
from modules.crm.deals.router import router
from modules.crm.deals.schemas import DealDTO
from modules.crm.deals.service import DealService

__all__ = [
    "DealDTO",
    "DealService",
    "install",
    "router",
]


def install() -> None:
    """Startup wiring for the feature (called by the loader when crm is enabled)."""
    register_crm_deals_rbac()
