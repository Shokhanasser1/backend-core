"""core/admin — the admin scaffold: authorization, permissions, screen registry.

Admin has no business logic and no tables of its own (schema §2.6): it is the
mechanism by which core modules and business features expose admin screens. A
module declares an ``AdminScreen`` in its ``admin.py`` and registers it; the app
mounts every registered screen under ``/api/admin/{slug}`` at startup and gates
each behind the same RBAC as everything else (interfaces §3.6, §5.4).
"""
