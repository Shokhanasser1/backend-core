"""Audit is a wildcard bus sink (interfaces doc §3.5).

Subscribing to ``*`` (a privilege of core modules) means adding commerce or any
future module needs no change here — core/audit never learns their event names.
Runs reliably under app_maintenance so it can also write system rows
(tenant_id NULL) for platform events like auth.user.*.
"""

from core.audit.service import AuditService
from shared.events import EventEnvelope, bus
from shared.handler_runtime import current_handler_runtime


@bus.subscribe("*", reliable=True, maintenance=True)
async def audit_sink(event: EventEnvelope) -> None:
    runtime = current_handler_runtime()
    await AuditService(runtime.uow.session, runtime.ctx).record_event(event)
