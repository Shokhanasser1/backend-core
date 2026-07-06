"""Machine-readable endpoint markers (interfaces doc §5.2, §5.3).

Every route must carry exactly one marker; the startup validator walks all
routes and refuses to start the application otherwise. The dependency
factories themselves (require_permission, authenticated_endpoint,
public_endpoint) live in core/auth (Phase 2) — markers are in shared/ so both
core (producers) and the app composition root (validator) can import them
without violating the dependency direction.
"""

PERMISSION_ATTR = "__permission__"
AUTHENTICATED_ATTR = "__authenticated__"
PUBLIC_ATTR = "__public__"

ALL_MARKER_ATTRS = (PERMISSION_ATTR, AUTHENTICATED_ATTR, PUBLIC_ATTR)
