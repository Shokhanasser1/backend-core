"""core/auth HTTP endpoints (/api/auth). Every route carries exactly one marker."""

from uuid import UUID

from fastapi import APIRouter, Depends, status

from core.auth.deps import (
    ServiceBundle,
    authed_bundle,
    authenticated_endpoint,
    public_bundle,
    public_endpoint,
)
from core.auth.schemas import (
    ChangePasswordIn,
    LoginIn,
    LoginResult,
    PasswordResetIn,
    PasswordResetRequestIn,
    RecoveryCodes,
    RefreshIn,
    RegisterIn,
    TokenPair,
    TotpConfirmIn,
    TotpSetup,
    TwoFactorVerifyIn,
    UserDTO,
)
from shared.errors import PermissionDeniedError

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(public_endpoint("registration is open to anonymous users"))],
)
async def register(body: RegisterIn, bundle: ServiceBundle = Depends(public_bundle)) -> UserDTO:
    return await bundle.auth.register(body.email, body.password, body.locale)


@router.post(
    "/login",
    dependencies=[Depends(public_endpoint("login authenticates by password"))],
)
async def login(body: LoginIn, bundle: ServiceBundle = Depends(public_bundle)) -> LoginResult:
    return await bundle.auth.authenticate(
        body.email,
        body.password,
        ip=bundle.request.ip,
        user_agent=bundle.request.user_agent,
    )


@router.post(
    "/2fa/verify",
    dependencies=[Depends(public_endpoint("2FA verify exchanges a challenge token"))],
)
async def verify_two_factor(
    body: TwoFactorVerifyIn, bundle: ServiceBundle = Depends(public_bundle)
) -> TokenPair:
    return await bundle.auth.complete_two_factor(body.challenge_token, body.code)


@router.post(
    "/refresh",
    dependencies=[Depends(public_endpoint("refresh presents a refresh token"))],
)
async def refresh(body: RefreshIn, bundle: ServiceBundle = Depends(public_bundle)) -> TokenPair:
    return await bundle.auth.refresh(body.refresh_token)


@router.post(
    "/password-reset/request",
    dependencies=[Depends(public_endpoint("password reset request is anonymous"))],
)
async def request_password_reset(
    body: PasswordResetRequestIn, bundle: ServiceBundle = Depends(public_bundle)
) -> dict[str, str | None]:
    # Always the same response (no user enumeration). The raw token is returned
    # here in v1 until Phase 3 delivers it via NotificationService.
    token = await bundle.auth.request_password_reset(body.email)
    return {"status": "ok", "reset_token": token}


@router.post(
    "/password-reset",
    dependencies=[Depends(public_endpoint("password reset presents a one-time token"))],
)
async def reset_password(
    body: PasswordResetIn, bundle: ServiceBundle = Depends(public_bundle)
) -> dict[str, str]:
    await bundle.auth.reset_password(body.reset_token, body.new_password)
    return {"status": "ok"}


@router.get(
    "/me",
    dependencies=[Depends(authenticated_endpoint("returns the caller's own profile"))],
)
async def me(bundle: ServiceBundle = Depends(authed_bundle)) -> UserDTO:
    return await bundle.auth.get_user(bundle.user_id)


@router.post(
    "/logout",
    dependencies=[Depends(authenticated_endpoint("revokes the caller's refresh token"))],
)
async def logout(body: RefreshIn, bundle: ServiceBundle = Depends(authed_bundle)) -> dict[str, str]:
    await bundle.auth.logout(body.refresh_token)
    return {"status": "ok"}


@router.post(
    "/change-password",
    dependencies=[Depends(authenticated_endpoint("changes the caller's own password"))],
)
async def change_password(
    body: ChangePasswordIn, bundle: ServiceBundle = Depends(authed_bundle)
) -> dict[str, str]:
    await bundle.auth.change_password(body.current_password, body.new_password)
    return {"status": "ok"}


@router.post(
    "/2fa/enable",
    dependencies=[Depends(authenticated_endpoint("starts 2FA enrollment for the caller"))],
)
async def enable_two_factor(bundle: ServiceBundle = Depends(authed_bundle)) -> TotpSetup:
    return await bundle.auth.enable_totp()


@router.post(
    "/2fa/confirm",
    dependencies=[Depends(authenticated_endpoint("confirms 2FA enrollment for the caller"))],
)
async def confirm_two_factor(
    body: TotpConfirmIn, bundle: ServiceBundle = Depends(authed_bundle)
) -> RecoveryCodes:
    return await bundle.auth.confirm_totp(body.code)


@router.post(
    "/2fa/disable",
    dependencies=[Depends(authenticated_endpoint("disables 2FA for the caller"))],
)
async def disable_two_factor(
    body: TotpConfirmIn, bundle: ServiceBundle = Depends(authed_bundle)
) -> dict[str, str]:
    await bundle.auth.disable_totp(body.code)
    return {"status": "ok"}


@router.post(
    "/tenants/{tenant_id}/token",
    dependencies=[Depends(authenticated_endpoint("exchanges a user session for a tenant token"))],
)
async def select_tenant(
    tenant_id: UUID, bundle: ServiceBundle = Depends(authed_bundle)
) -> TokenPair:
    """Issue a tenant-scoped access token after verifying membership (OV-03)."""
    if not await bundle.tenants.has_active_membership(tenant_id, bundle.user_id):
        raise PermissionDeniedError("not a member of this tenant")
    return await bundle.auth.issue_tenant_token(tenant_id)
