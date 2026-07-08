"""saas.metering — usage metering (a generic recording/reporting primitive).

The loader (app/features.py) treats a feature package as two optional hooks:
``install()`` (registers RBAC at startup) and ``router`` (an APIRouter it mounts).
Everything else is internal to the feature; only MeteringService (and its DTO) is
a public interface (§1.2) that callers import from here (the package). No bus
subscribers: usage is recorded through explicit ``MeteringService.record`` calls
(owner decision), so a generic meter never hardwires other modules' event names.
"""

from modules.saas.metering.permissions import register_saas_metering_rbac
from modules.saas.metering.router import router
from modules.saas.metering.schemas import UsageWindowDTO
from modules.saas.metering.service import MeteringService

__all__ = ["MeteringService", "UsageWindowDTO", "install", "router"]


def install() -> None:
    """Startup wiring for the feature (called by the loader when saas is enabled)."""
    register_saas_metering_rbac()
