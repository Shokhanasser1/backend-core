"""core/audit — append-only log of significant actions (who, what, when, whence).

Minimal in Phase 2 (decision OV-26): AuditService.record() + a wildcard bus
sink. Reading, admin screens and retention arrive in Phase 4.
"""
