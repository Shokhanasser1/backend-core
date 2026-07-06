"""Public DTOs of core/auth (Pydantic frozen; ORM never crosses the boundary)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class UserDTO(_Frozen):
    id: UUID
    email: str
    full_name: str | None
    phone: str | None
    locale: str
    two_factor_enabled: bool


class TokenPair(_Frozen):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - protocol label, not a secret
    expires_in: int  # access token TTL seconds


class TwoFactorChallenge(_Frozen):
    challenge_token: str
    two_factor_required: bool = True


class TotpSetup(_Frozen):
    secret: str
    otpauth_uri: str


# authenticate() returns either tokens or a 2FA challenge.
LoginResult = TokenPair | TwoFactorChallenge


class RecoveryCodes(_Frozen):
    codes: list[str]


# --- request bodies (boundary validation; extra="forbid" blocks mass assignment) ---


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterIn(_StrictIn):
    email: str
    password: str
    full_name: str | None = None
    locale: str = "ru"


class LoginIn(_StrictIn):
    email: str
    password: str


class TwoFactorVerifyIn(_StrictIn):
    challenge_token: str
    code: str


class RefreshIn(_StrictIn):
    refresh_token: str


class ChangePasswordIn(_StrictIn):
    current_password: str
    new_password: str


class PasswordResetRequestIn(_StrictIn):
    email: str


class PasswordResetIn(_StrictIn):
    reset_token: str
    new_password: str


class TotpConfirmIn(_StrictIn):
    code: str
