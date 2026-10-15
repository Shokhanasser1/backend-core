"""crm.deals <- crm.contacts: clear links to deleted contacts/companies.

A deal stores the ids of its contact/company by value (no cross-feature FK), so a
deletion in crm.contacts must be mirrored here or the deal keeps a dangling id.
Reliable subscribers keyed to explicit event names (features may not use
wildcards — §1.1); they run in the worker's UoW + tenant context (from the
envelope). Registered by importing this module in the feature's ``install()``
(web and worker), so the handlers are resolvable in the arq worker.
"""

from uuid import UUID

from modules.crm.contacts import ContactsService
from modules.crm.deals.service import DealService
from shared.events import EventEnvelope, bus
from shared.handler_runtime import current_handler_runtime


def _deals() -> DealService:
    runtime = current_handler_runtime()
    contacts = ContactsService(runtime.uow, runtime.bus, runtime.ctx)
    # The funnel is irrelevant to unlinking; stages are not consulted.
    return DealService(runtime.uow, runtime.bus, runtime.ctx, contacts=contacts, stages=())


@bus.subscribe("crm.contact.deleted", reliable=True)
async def on_contact_deleted(event: EventEnvelope) -> None:
    raw = event.payload.get("contact_id")
    if raw and current_handler_runtime().ctx.tenant_id is not None:
        await _deals().unlink_contact(UUID(str(raw)))


@bus.subscribe("crm.company.deleted", reliable=True)
async def on_company_deleted(event: EventEnvelope) -> None:
    raw = event.payload.get("company_id")
    if raw and current_handler_runtime().ctx.tenant_id is not None:
        await _deals().unlink_company(UUID(str(raw)))
