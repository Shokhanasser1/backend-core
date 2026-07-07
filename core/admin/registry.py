"""Admin screen registry (interfaces §3.6, §5.4).

Symmetric to ``register_permissions`` / ``register_templates``: each module
declares its admin screen(s) at import time and registers them on this
process-global registry. The app reads ``screens()`` at startup to mount routers
and validate them. A disabled module is never imported, so its screens simply do
not exist — there is no config to toggle them off.

An ``AdminScreen`` is a slug (URL segment), an ``APIRouter`` (the screen's
endpoints, each of which MUST carry ``require_permission``), and the permission
that gates the screen's visibility in the menu.

Not public (schema §3.6): the mounting of routers and the menu router itself.
What a module touches is exactly ``AdminScreen`` + ``admin_registry.register``.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import APIRouter

# The single mount point for every admin screen. Kept here (not in router.py) so
# both the router and the service can reference it without an import cycle.
ADMIN_PREFIX = "/api/admin"

# Slug is a single URL path segment: lowercase, starts with a letter.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class AdminScreen:
    slug: str  # unique; URL segment mounted at /api/admin/{slug}
    title_key: str  # i18n catalog key for the admin-UI label
    module: str  # owning core module ("audit") or feature ("commerce.orders") — diagnostics
    router: APIRouter  # screen endpoints; each MUST carry require_permission (§5.4)
    permission: str  # permission gating menu visibility, e.g. "audit.record:read"


class AdminRegistry:
    def __init__(self) -> None:
        self._by_slug: dict[str, AdminScreen] = {}

    def register(self, screen: AdminScreen) -> None:
        """Register a screen. A duplicate slug is a startup error; re-registering
        the identical screen (a new app instance in the same process) is a no-op."""
        if not _SLUG_RE.match(screen.slug):
            raise ValueError(
                f"invalid admin screen slug {screen.slug!r}: expected a lowercase URL segment"
            )
        existing = self._by_slug.get(screen.slug)
        if existing is not None:
            if existing is screen:
                return  # idempotent: same screen object re-registered
            raise RuntimeError(f"duplicate admin screen slug registered: {screen.slug}")
        self._by_slug[screen.slug] = screen

    def screens(self) -> Sequence[AdminScreen]:
        """All registered screens, ordered by slug for a stable menu/validation."""
        return tuple(screen for _, screen in sorted(self._by_slug.items()))

    def reset(self) -> None:
        """For tests: clear the registry between app instances."""
        self._by_slug.clear()


# Process-global registry; modules register on it at import time.
admin_registry = AdminRegistry()
