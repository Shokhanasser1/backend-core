"""commerce.product_images — staff-managed images for products.

Requires the ``commerce.products`` feature (validates the product) and the
``files`` core module (stores the bytes). The loader (app/features.py) treats the
package as two hooks: ``install()`` (registers RBAC at startup) and ``router``.
"""

from modules.commerce.product_images.permissions import register_product_images_rbac
from modules.commerce.product_images.router import router
from modules.commerce.product_images.schemas import ProductImageDTO
from modules.commerce.product_images.service import ProductImageService

__all__ = ["ProductImageDTO", "ProductImageService", "install", "router"]


def install() -> None:
    """Startup wiring for the feature (called by the loader when commerce is enabled)."""
    register_product_images_rbac()
