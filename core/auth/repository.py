"""Repositories for auth global tables (no RLS; access encapsulated here)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.models import User
from shared.repository import GlobalRepository


class UserRepository(GlobalRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(func.lower(User.email) == email.strip().lower())
        return (await self._session.execute(stmt)).scalar_one_or_none()


def make_user_repository(session: AsyncSession) -> UserRepository:
    return UserRepository(session)
