"""Public read-only view of user contact details (interfaces §3.4 support).

Sibling modules (notifications) resolve a UserRecipient's addresses and locale
without touching the users table directly or seeing the User ORM — the boundary
stays a DTO (ADR-0005). This is the auth module's public interface, mirroring how
billing consumes AuditService.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.repository import UserRepository


@dataclass(frozen=True, slots=True)
class UserContact:
    user_id: UUID
    email: str | None
    phone: str | None  # E.164
    locale: str


class UserDirectory:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)

    async def get_contact(self, user_id: UUID) -> UserContact | None:
        user = await self._users.get(user_id)
        if user is None:
            return None
        return UserContact(user_id=user.id, email=user.email, phone=user.phone, locale=user.locale)
