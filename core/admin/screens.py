"""Registration of the core admin screens (interfaces §5.4).

Explicit and re-runnable: ``create_app`` calls ``admin_registry.reset()`` then
``register_admin_screens()`` on every app instance, so the registry reflects
exactly this app's screens. Feature screens are registered separately by the
feature loader (each feature's ``install()``), never here — a disabled module
contributes nothing.
"""

from core.admin.registry import admin_registry
from core.audit.admin import AUDIT_SCREEN


def register_admin_screens() -> None:
    admin_registry.register(AUDIT_SCREEN)
