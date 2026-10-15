"""crm.activities <- crm.contacts / crm.deals: clear links to deleted entities.

An activity stores the ids of its contact/company/deal by value (no cross-feature
FK), so a deletion in a sibling must be mirrored here or the activity keeps a
dangling id — the activity itself (the history) stays. Reliable subscribers keyed
to explicit event names (features may not use wildcards — §1.1); they run in the
worker's UoW + tenant context (from the envelope). Registered by importing this
module in the feature's ``install()`` (web and worker).
"""

from uuid import UUID

from modules.crm.activities.service import ActivityService
from modules.crm.contacts import ContactsService
from modules.crm.deals import DealService
from shared.events import EventEnvelope, bus
from shared.handler_runtime import current_handler_runtime


def _activities() -> ActivityService:
    runtime = current_handler_runtime()
    contacts = ContactsService(runtime.uow, runtime.bus, runtime.ctx)
    deals = DealService(runtime.uow, runtime.bus, runtime.ctx, contacts=contacts, stages=())
    # Types are irrelevant to unlinking; the configuration is not consulted.
    return ActivityService(
        runtime.uow, runtime.bus, runtime.ctx, contacts=contacts, deals=deals, types=()
    )


def _id(event: EventEnvelope, key: str) -> UUID | None:
    raw = event.payload.get(key)
    if not raw or current_handler_runtime().ctx.tenant_id is None:
        return None
    return UUID(str(raw))


@bus.subscribe("crm.contact.deleted", reliable=True)
async def on_contact_deleted(event: EventEnvelope) -> None:
    contact_id = _id(event, "contact_id")
    if contact_id is not None:
        await _activities().unlink_contact(contact_id)


@bus.subscribe("crm.company.deleted", reliable=True)
async def on_company_deleted(event: EventEnvelope) -> None:
    company_id = _id(event, "company_id")
    if company_id is not None:
        await _activities().unlink_company(company_id)


@bus.subscribe("crm.deal.deleted", reliable=True)
async def on_deal_deleted(event: EventEnvelope) -> None:
    deal_id = _id(event, "deal_id")
    if deal_id is not None:
        await _activities().unlink_deal(deal_id)
