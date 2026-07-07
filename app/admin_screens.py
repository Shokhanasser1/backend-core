"""Composition-root wiring for admin screens (interfaces §5.4).

Mounts every registered ``AdminScreen`` under ``/api/admin/{slug}`` and checks
that each screen's gating permission exists in the catalog. Lives in app/ (the
composition root) because it wires the FastAPI application; core/admin only
provides the registry, keeping the dependency direction app -> core -> shared.
The screens must already be registered (``register_admin_screens()``) and all
permissions declared before this runs.
"""

from fastapi import FastAPI

from core.admin.registry import ADMIN_PREFIX, admin_registry
from core.auth.permissions import permission_registry


def mount_admin_screens(app: FastAPI) -> None:
    for screen in admin_registry.screens():
        # A screen gated by an unregistered permission is a wiring bug — fail loudly.
        permission_registry.require_registered(screen.permission)
        app.include_router(screen.router, prefix=f"{ADMIN_PREFIX}/{screen.slug}")
