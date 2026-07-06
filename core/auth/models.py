"""Auth ORM models — global tables (schema §2.1, decision OV-01).

Users are global (one account across all tenants); TOTP, recovery codes and
refresh tokens follow the user, so they are global too. These are internals of
core/auth: no other module reads these tables (ADR-0005).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import GlobalBase, TimestampMixin
from shared.ids import new_uuid7


class User(TimestampMixin, GlobalBase):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    email: Mapped[str] = mapped_column(Text, nullable=False)  # normalised to lower in the app
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)  # argon2id PHC string
    full_name: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)  # E.164
    locale: Mapped[str] = mapped_column(Text, nullable=False, default="ru")
    is_superuser: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('active', 'blocked')", name="status"),
        # Case-insensitive uniqueness without the citext extension (portability).
        Index("uq_users_email_lower", func.lower(email), unique=True),
    )


class UserTotp(TimestampMixin, GlobalBase):
    __tablename__ = "user_totp"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # App-level encrypted (Fernet/MultiFernet, key from env — OV-19).
    secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # NULL = enrollment not finished; 2FA is on iff NOT NULL.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRecoveryCode(TimestampMixin, GlobalBase):
    __tablename__ = "user_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # SHA-256: codes carry >= 64 bits of entropy, so a slow hash is unnecessary.
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_user_recovery_codes_user_id", "user_id"),)


class RefreshToken(TimestampMixin, GlobalBase):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # One login = one family; the whole rotation chain shares family_id.
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # SHA-256 of the raw token (256 bits of entropy); the raw token is never stored.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 'logout' | 'reuse_detected' | 'admin' | 'password_change'
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("uq_refresh_tokens_token_hash", "token_hash", unique=True),
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )
