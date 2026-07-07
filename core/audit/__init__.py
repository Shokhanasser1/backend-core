"""core/audit — append-only log of significant actions (who, what, when, whence).

Started minimal in Phase 2 (decision OV-26): AuditService.record() + a wildcard
bus sink. Completed in Phase 4: AuditService.search() (the reading side), the
audit admin screen (admin.py), and the retention sweep (retention.py, run as
app_retention — the only role that may erase the journal).
"""
