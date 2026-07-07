"""commerce.cart — buyer's cart (requires commerce.products).

The loader mounts ``router``. cart owns no RBAC codes, templates or admin screens
(it is entirely buyer-facing via authenticated_endpoint + ownership), so it needs
no ``install()`` hook.
"""

from modules.commerce.cart.router import router

__all__ = ["router"]
