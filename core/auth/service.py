"""AuthService — full auth lifecycle (interfaces §3.1; threat model V2/V3).

Depends on Redis (rate limiting, lockout, TOTP replay, ephemeral token stores),
settings (TTLs, JWT secret) and a SecretCipher (encrypting TOTP secrets). All
user-visible failures are deliberately non-specific (no user enumeration).
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult

from core.auth.models import RefreshToken, User, UserRecoveryCode, UserTotp
from core.auth.notifications import PasswordResetMailer
from core.auth.rate_limit import (
    EphemeralTokenStore,
    LoginThrottle,
    RateLimiter,
    TotpReplayGuard,
)
from core.auth.repository import UserRepository
from core.auth.schemas import (
    AccessToken,
    LoginResult,
    RecoveryCodes,
    TokenPair,
    TotpSetup,
    TwoFactorChallenge,
    UserDTO,
)
from core.auth.security import passwords, totp
from core.auth.security.tokens import encode_access_token
from shared.config import Settings
from shared.context import TenantContext
from shared.encryption import SecretCipher
from shared.errors import (
    AuthenticationError,
    ConflictError,
    InvariantViolationError,
    NotFoundError,
    RateLimitedError,
)
from shared.events import EventBus
from shared.ids import new_uuid7
from shared.security_tokens import hash_token, new_opaque_token
from shared.service import Service, UnitOfWork

CHALLENGE_NS = "auth:2fa"
RESET_NS = "auth:pwreset"


def _user_dto(user: User, *, two_factor_enabled: bool) -> UserDTO:
    return UserDTO(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        locale=user.locale,
        two_factor_enabled=two_factor_enabled,
    )


class AuthService(Service):
    def __init__(
        self,
        uow: UnitOfWork,
        bus: EventBus,
        ctx: TenantContext,
        *,
        redis: Redis,
        settings: Settings,
        cipher: SecretCipher,
        reset_mailer: PasswordResetMailer,
    ) -> None:
        super().__init__(uow, bus, ctx)
        self._session = uow.session
        self._users = UserRepository(uow.session)
        self._settings = settings
        self._cipher = cipher
        self._reset_mailer = reset_mailer
        self._limiter = RateLimiter(redis)
        self._throttle = LoginThrottle(
            redis,
            max_failures=settings.login_max_failures,
            lockout_seconds=settings.login_lockout_seconds,
        )
        self._replay = TotpReplayGuard(redis)
        self._challenges = EphemeralTokenStore(redis, CHALLENGE_NS)
        self._resets = EphemeralTokenStore(redis, RESET_NS)

    # ------------------------------------------------------------- registration
    async def register(
        self, email: str, password: str, locale: str = "ru", *, ip: str | None = None
    ) -> UserDTO:
        # Per-IP cap: anti-abuse, and it bounds probing emails via the 409 below.
        if not await self._limiter.hit(
            "register_ip",
            ip or "unknown",
            limit=self._settings.register_ip_limit_per_hour,
            window_seconds=3600,
        ):
            raise RateLimitedError("too many registrations")
        normalized = email.strip().lower()
        if await self._users.get_by_email(normalized) is not None:
            raise ConflictError("email already registered")
        user = User(
            id=new_uuid7(),
            email=normalized,
            password_hash=passwords.hash_password(password),
            locale=locale,
            status="active",
        )
        await self._users.add(user)
        # Events (and the audit trail fed from them) carry ids, never the email.
        self.emit("auth.user.registered", {"user_id": str(user.id), "locale": user.locale})
        return _user_dto(user, two_factor_enabled=False)

    # --------------------------------------------------------------------- login
    async def authenticate(
        self, email: str, password: str, *, ip: str | None, user_agent: str | None
    ) -> LoginResult:
        normalized = email.strip().lower()
        if not await self._limiter.hit("login_ip", ip or "unknown", limit=30, window_seconds=60):
            raise RateLimitedError("too many attempts")

        user = await self._users.get_by_email(normalized)
        account_key = str(user.id) if user else f"email:{normalized}"
        if await self._throttle.is_locked(account_key):
            raise RateLimitedError("account temporarily locked")

        password_ok = passwords.verify_password(user.password_hash if user else None, password)
        if user is None or not password_ok or user.status != "active":
            await self._throttle.record_failure(account_key)
            # The request ends in a rollback, so a transactional emit would be
            # discarded: publish now. No email (PII) — the user id when known.
            await self.emit_now(
                "auth.user.login_failed",
                {
                    "user_id": str(user.id) if user else None,
                    "ip": ip,
                    "reason": "invalid_credentials",
                },
            )
            raise AuthenticationError("invalid credentials")

        await self._throttle.clear(account_key)

        totp_row = await self._session.get(UserTotp, user.id)
        if totp_row is not None and totp_row.confirmed_at is not None:
            return await self._issue_two_factor_challenge(user.id)

        return await self._issue_tokens(user, ip=ip, user_agent=user_agent)

    async def _issue_two_factor_challenge(self, user_id: UUID) -> TwoFactorChallenge:
        raw = new_opaque_token()
        await self._challenges.put(
            hash_token(raw),
            str(user_id),
            ttl_seconds=self._settings.two_factor_challenge_ttl_seconds,
        )
        return TwoFactorChallenge(challenge_token=raw)

    async def complete_two_factor(self, challenge_token: str, totp_code: str) -> TokenPair:
        user_id_raw = await self._challenges.consume(hash_token(challenge_token))
        if user_id_raw is None:
            raise AuthenticationError("invalid or expired challenge")
        user_id = UUID(user_id_raw)
        if not await self._limiter.hit("2fa", str(user_id), limit=5, window_seconds=300):
            raise RateLimitedError("too many 2FA attempts")
        user = await self._users.get(user_id)
        if user is None or user.status != "active":
            raise AuthenticationError("invalid or expired challenge")  # blocked meanwhile
        if not await self._verify_totp_or_recovery(user_id, totp_code):
            raise AuthenticationError("invalid 2FA code")
        return await self._issue_tokens(user, ip=None, user_agent=None)

    # -------------------------------------------------------------------- tokens
    async def _issue_tokens(
        self, user: User, *, ip: str | None, user_agent: str | None
    ) -> TokenPair:
        access, _jti = encode_access_token(user_id=user.id, tenant_id=None, settings=self._settings)
        raw_refresh = new_opaque_token()
        self._session.add(
            RefreshToken(
                id=new_uuid7(),
                user_id=user.id,
                family_id=new_uuid7(),
                token_hash=hash_token(raw_refresh),
                expires_at=datetime.now(UTC)
                + timedelta(seconds=self._settings.refresh_token_ttl_seconds),
                ip=ip,
                user_agent=user_agent,
            )
        )
        user.last_login_at = datetime.now(UTC)
        await self._session.flush()
        self.emit(
            "auth.user.login_succeeded",
            {"user_id": str(user.id), "ip": ip or "", "user_agent": user_agent or ""},
        )
        return TokenPair(
            access_token=access,
            refresh_token=raw_refresh,
            expires_in=self._settings.access_token_ttl_seconds,
        )

    async def refresh(self, refresh_token: str) -> TokenPair:
        token_hash = hash_token(refresh_token)
        row = (
            await self._session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AuthenticationError("invalid refresh token")

        # Reuse of a rotated/revoked token → kill the whole family (V3). The
        # revocation is committed even though the request ends in a 401 — a
        # raised error would otherwise roll it back. (Reuse auditing lands with
        # the audit read side in Phase 4.)
        if row.rotated_at is not None or row.revoked_at is not None:
            await self._revoke_family(row.family_id, reason="reuse_detected")
            await self._session.commit()
            raise AuthenticationError("refresh token reuse detected")
        if row.expires_at <= datetime.now(UTC):
            raise AuthenticationError("refresh token expired")
        user = await self._users.get(row.user_id)
        if user is None or user.status != "active":
            # A blocked account loses its sessions for good, not just this call.
            await self._revoke_family(row.family_id, reason="user_blocked")
            await self._session.commit()
            raise AuthenticationError("invalid refresh token")

        # Atomic CAS rotation: only one caller wins.
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.id == row.id,
                    RefreshToken.rotated_at.is_(None),
                    RefreshToken.revoked_at.is_(None),
                )
                .values(rotated_at=datetime.now(UTC))
            ),
        )
        if result.rowcount == 0:
            raise AuthenticationError("refresh token already rotated")

        access, _jti = encode_access_token(user_id=user.id, tenant_id=None, settings=self._settings)
        raw_refresh = new_opaque_token()
        self._session.add(
            RefreshToken(
                id=new_uuid7(),
                user_id=user.id,
                family_id=row.family_id,
                token_hash=hash_token(raw_refresh),
                expires_at=datetime.now(UTC)
                + timedelta(seconds=self._settings.refresh_token_ttl_seconds),
            )
        )
        await self._session.flush()
        return TokenPair(
            access_token=access,
            refresh_token=raw_refresh,
            expires_in=self._settings.access_token_ttl_seconds,
        )

    async def issue_tenant_token(self, tenant_id: UUID) -> AccessToken:
        """Exchange a user-scope session for a tenant-scoped access token (OV-03).
        Membership (and the tenant being active) is verified by the caller before
        this is called. Access token only: the long-lived session stays the one
        refresh family from login — a stolen access token cannot mint another."""
        user = await self._users.get_or_raise(self._actor_user_id())
        if user.status != "active":
            raise AuthenticationError("account is not active")
        access, _jti = encode_access_token(
            user_id=user.id, tenant_id=tenant_id, settings=self._settings
        )
        return AccessToken(access_token=access, expires_in=self._settings.access_token_ttl_seconds)

    async def logout(self, refresh_token: str) -> None:
        token_hash = hash_token(refresh_token)
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), revoked_reason="logout")
        )

    async def _revoke_family(self, family_id: UUID, *, reason: str) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
        )

    async def _revoke_all_user_tokens(self, user_id: UUID, *, reason: str) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
        )

    # ---------------------------------------------------------------- passwords
    async def change_password(self, current_password: str, new_password: str) -> None:
        user = await self._users.get_or_raise(self._actor_user_id())
        if not passwords.verify_password(user.password_hash, current_password):
            raise AuthenticationError("current password is incorrect")
        user.password_hash = passwords.hash_password(new_password)
        await self._session.flush()
        await self._revoke_all_user_tokens(user.id, reason="password_change")
        self.emit("auth.user.password_changed", {"user_id": str(user.id)})

    async def request_password_reset(self, email: str) -> None:
        """Always succeeds silently (no user enumeration). The one-time token is
        delivered only inside the emailed link — never returned or emitted."""
        user = await self._users.get_by_email(email.strip().lower())
        if user is None:
            return
        # Over the limit: drop silently — an error would be an enumeration oracle,
        # and the cap keeps the endpoint from being used to flood an inbox.
        if not await self._limiter.hit("pwreset", str(user.id), limit=3, window_seconds=900):
            return
        raw = new_opaque_token()
        ttl = self._settings.password_reset_ttl_seconds
        await self._resets.put(hash_token(raw), str(user.id), ttl_seconds=ttl)
        await self._reset_mailer.send(
            email=user.email,
            locale=user.locale,
            reset_url=self._settings.password_reset_url.replace("{token}", raw),
            ttl_minutes=ttl // 60,
        )
        self.emit("auth.user.password_reset_requested", {"user_id": str(user.id)})

    async def reset_password(self, reset_token: str, new_password: str) -> None:
        user_id_raw = await self._resets.consume(hash_token(reset_token))
        if user_id_raw is None:
            raise AuthenticationError("invalid or expired reset token")
        user = await self._users.get_or_raise(UUID(user_id_raw))
        user.password_hash = passwords.hash_password(new_password)
        await self._session.flush()
        await self._revoke_all_user_tokens(user.id, reason="password_change")

    # --------------------------------------------------------------------- 2FA
    async def enable_totp(self) -> TotpSetup:
        user = await self._users.get_or_raise(self._actor_user_id())
        secret = totp.generate_totp_secret()
        existing = await self._session.get(UserTotp, user.id)
        if existing is not None:
            if existing.confirmed_at is not None:
                raise ConflictError("2FA already enabled")
            existing.secret_encrypted = self._cipher.encrypt(secret)
        else:
            self._session.add(
                UserTotp(user_id=user.id, secret_encrypted=self._cipher.encrypt(secret))
            )
        await self._session.flush()
        return TotpSetup(secret=secret, otpauth_uri=totp.provisioning_uri(secret, user.email))

    async def confirm_totp(self, totp_code: str) -> RecoveryCodes:
        user_id = self._actor_user_id()
        row = await self._session.get(UserTotp, user_id)
        if row is None:
            raise InvariantViolationError("start 2FA enrollment first")
        secret = self._cipher.decrypt(row.secret_encrypted)
        if not totp.verify_totp(secret, totp_code):
            raise AuthenticationError("invalid 2FA code")
        row.confirmed_at = datetime.now(UTC)
        codes = totp.generate_recovery_codes()
        for code in codes:
            self._session.add(
                UserRecoveryCode(
                    id=new_uuid7(), user_id=user_id, code_hash=totp.hash_recovery_code(code)
                )
            )
        await self._session.flush()
        self.emit("auth.user.two_factor_enabled", {"user_id": str(user_id)})
        return RecoveryCodes(codes=codes)

    async def disable_totp(self, totp_code: str) -> None:
        user_id = self._actor_user_id()
        if not await self._verify_totp_or_recovery(user_id, totp_code):
            raise AuthenticationError("invalid 2FA code")
        row = await self._session.get(UserTotp, user_id)
        if row is not None:
            await self._session.delete(row)
        await self._session.execute(
            delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id)
        )
        await self._session.flush()
        self.emit("auth.user.two_factor_disabled", {"user_id": str(user_id)})

    async def _verify_totp_or_recovery(self, user_id: UUID, code: str) -> bool:
        row = await self._session.get(UserTotp, user_id)
        if row is None:
            return False
        secret = self._cipher.decrypt(row.secret_encrypted)
        if totp.verify_totp(secret, code):
            # Reject a replayed code within its validity window.
            return await self._replay.check_and_mark(str(user_id), code)
        # Fall back to a one-time recovery code: spent by an atomic compare-and-set,
        # so two concurrent logins with the same code cannot both succeed.
        spent = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(UserRecoveryCode)
                .where(
                    UserRecoveryCode.user_id == user_id,
                    UserRecoveryCode.code_hash == totp.hash_recovery_code(code),
                    UserRecoveryCode.used_at.is_(None),
                )
                .values(used_at=datetime.now(UTC))
            ),
        )
        return spent.rowcount == 1

    # -------------------------------------------------------------------- reads
    async def get_user(self, user_id: UUID) -> UserDTO:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("user not found")
        totp_row = await self._session.get(UserTotp, user_id)
        enabled = totp_row is not None and totp_row.confirmed_at is not None
        return _user_dto(user, two_factor_enabled=enabled)

    def _actor_user_id(self) -> UUID:
        if self.ctx.actor.kind != "user" or not self.ctx.actor.id:
            raise InvariantViolationError("operation requires a user actor")
        return UUID(self.ctx.actor.id)
