"""Tenants -> notifications: the invitation email.

Tenants owns the message SEMANTICS (template + when to send); notifications is
pure infrastructure. The raw invitation token leaves the server only inside the
emailed accept link — never in the API response or on the bus. The row is queued
in the inviting tenant's outbox in the SAME transaction as the invitation (tenant
context, app_user), so there is no invitation without its email and vice versa.
The template is registered by importing this module in ``core/subscribers.py``
(both web and worker), so the dispatcher can render it.
"""

from pathlib import Path

from core.notifications.registry import TemplateDef, register_templates, template_registry
from core.notifications.schemas import AddressRecipient
from core.notifications.service import NotificationService
from shared.context import TenantContext
from shared.encryption import SecretCipher
from shared.events import EventBus
from shared.service import UnitOfWork

INVITATION_TEMPLATE = "tenants.invitation"

TENANTS_TEMPLATES = [
    TemplateDef(
        INVITATION_TEMPLATE, ("email",), frozenset({"accept_url", "tenant_name", "ttl_days"})
    ),
]

register_templates("tenants", TENANTS_TEMPLATES, Path(__file__).parent / "templates")


class InvitationMailer:
    def __init__(self, cipher: SecretCipher, accept_url_template: str) -> None:
        self._cipher = cipher
        self._accept_url_template = accept_url_template

    async def send(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        email: str,
        token: str,
        tenant_name: str,
        locale: str,
        ttl_days: int,
    ) -> None:
        service = NotificationService(
            uow, bus, ctx, registry=template_registry, cipher=self._cipher
        )
        await service.send(
            AddressRecipient("email", email),
            INVITATION_TEMPLATE,
            {
                "accept_url": self._accept_url_template.replace("{token}", token),
                "tenant_name": tenant_name,
                "ttl_days": ttl_days,
            },
            locale=locale,
        )
