"""Alembic root: env.py + config only (decision OV-10, ADR-0008).

Revision files live next to their components: shared/migrations,
core/<module>/migrations (Phase 2+), modules/<module>/<feature>/migrations
(Phase 6+) — each in its own branch with a branch label, applied with
``alembic upgrade heads`` (plural, always).
"""
