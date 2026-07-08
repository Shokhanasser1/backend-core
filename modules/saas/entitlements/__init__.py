"""saas.entitlements — the module's foundation feature (tariff entitlements).

The loader (app/features.py) treats a feature package as two optional hooks:
``install()`` (registers RBAC + bus subscribers at startup) and ``router`` (an
APIRouter it mounts). Everything else — models, service, repo — is internal to
the feature; only EntitlementService (and its DTO) is a public interface (§1.2)
that sibling features import from here (the package), never from the submodule.
"""

from modules.saas.entitlements.permissions import register_saas_entitlements_rbac
from modules.saas.entitlements.router import router
from modules.saas.entitlements.schemas import EntitlementsDTO
from modules.saas.entitlements.service import EntitlementService

__all__ = ["EntitlementService", "EntitlementsDTO", "install", "router"]


def install() -> None:
    """Startup wiring for the feature (called by the loader when saas is enabled)."""
    register_saas_entitlements_rbac()
    import modules.saas.entitlements.subscribers  # noqa: F401  (bus subscribers)
