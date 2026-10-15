"""Auth -> notifications: the password-reset email (threat model V2).

Auth owns the message SEMANTICS (template + when to send); notifications is pure
infrastructure and never knows about auth. The raw reset token leaves the server
only inside the emailed link — never in the API response or on the bus (events
carry ids only). The template is registered by importing this module in
``core/subscribers.py`` (both web and worker), so the dispatcher can render it.
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.notifications.registry import TemplateDef, register_templates, template_registry
from core.notifications.schemas import AddressRecipient
from core.notifications.service import NotificationService
from shared.context import Actor, TenantContext
from shared.encryption import SecretCipher
from shared.events import EventBus
from shared.service import SqlAlchemyUnitOfWork

PASSWORD_RESET_TEMPLATE = "auth.password_reset"  # noqa: S105 - a template key, not a secret

AUTH_TEMPLATES = [
    TemplateDef(PASSWORD_RESET_TEMPLATE, ("email",), frozenset({"reset_url", "ttl_minutes"})),
]

register_templates("auth", AUTH_TEMPLATES, Path(__file__).parent / "templates")


class PasswordResetMailer:
    """Queues the reset email as a platform send (tenant_id NULL).

    Platform outbox rows are writable only by app_maintenance (schema §3.4), and
    app_maintenance cannot read users — so the caller resolves the address under
    app_user and this mailer writes just the outbox row, in its own short
    maintenance transaction."""

    def __init__(
        self,
        maintenance_sessions: async_sessionmaker[AsyncSession],
        bus: EventBus,
        cipher: SecretCipher,
    ) -> None:
        self._sessions = maintenance_sessions
        self._bus = bus
        self._cipher = cipher

    async def send(self, *, email: str, locale: str, reset_url: str, ttl_minutes: int) -> None:
        ctx = TenantContext(
            tenant_id=None, actor=Actor(kind="system", id="auth.password_reset"), request_id=None
        )
        async with SqlAlchemyUnitOfWork(self._sessions, context=ctx) as uow:
            service = NotificationService(
                uow, self._bus, ctx, registry=template_registry, cipher=self._cipher
            )
            await service.send(
                AddressRecipient("email", email),
                PASSWORD_RESET_TEMPLATE,
                {"reset_url": reset_url, "ttl_minutes": ttl_minutes},
                locale=locale,
            )
