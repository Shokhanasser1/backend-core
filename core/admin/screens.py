"""Central registration of admin screens (mirrors core/subscribers).

Importing this module imports every enabled module's ``admin.py``, whose
module-level ``admin_registry.register(...)`` populates the registry. The app
imports this once at startup, then mounts what the registry holds. Business
modules (commerce, ...) add their screens the same way once enabled (Phase 6);
they are absent here until then, so nothing to toggle.
"""

import core.audit.admin  # noqa: F401  (registers the audit activity-log screen)


def register_admin_screens() -> None:
    """Idempotent no-op entry point — importing this module does the work.
    Exists so call sites read intentionally (`register_admin_screens()`)."""
