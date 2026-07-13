"""crm.contacts — people and companies (the CRM address book).

The loader (app/features.py) treats a feature package as two optional hooks:
``install()`` (registers RBAC at startup) and ``router`` (an APIRouter it mounts).
Everything else is internal to the feature; only ContactsService (and its DTOs)
is a public interface (§1.2) that callers import from here (the package). No bus
subscribers: contacts react to nothing — siblings (deals, tasks — later) will read
people/companies through ContactsService, never this feature's tables.
"""

from modules.crm.contacts.permissions import register_crm_contacts_rbac
from modules.crm.contacts.router import router
from modules.crm.contacts.schemas import CompanyDTO, ContactDTO
from modules.crm.contacts.service import ContactsService

__all__ = [
    "CompanyDTO",
    "ContactDTO",
    "ContactsService",
    "install",
    "router",
]


def install() -> None:
    """Startup wiring for the feature (called by the loader when crm is enabled)."""
    register_crm_contacts_rbac()
