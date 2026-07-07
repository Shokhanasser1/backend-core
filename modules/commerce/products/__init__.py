"""commerce.products — independent feature (the transferable catalog).

The loader (app/features.py) treats a feature package as two optional hooks:
``install()`` (registers RBAC/templates/subscribers/admin at startup) and
``router`` (an APIRouter it mounts). Everything else — models, service, repo — is
internal to the feature (only ProductService is a public interface, §1.2).
"""

from modules.commerce.products.permissions import register_products_rbac
from modules.commerce.products.router import router
from modules.commerce.products.schemas import ProductDTO
from modules.commerce.products.service import ProductService

# Public interface of the feature (§1.2): sibling features import ProductService
# from here (the package), never from the internal service module.
__all__ = ["ProductDTO", "ProductService", "install", "router"]


def install() -> None:
    """Startup wiring for the feature (called by the loader when commerce is enabled)."""
    register_products_rbac()
