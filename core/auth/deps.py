"""FastAPI dependencies: request-scoped services + permission declaration markers.

The three markers make the declaration mechanism (interfaces §5.2): every route
carries exactly one, and the startup validator refuses to boot otherwise.
``require_permission`` also enforces at runtime (router line) on top of the
service-layer check (second line).

These dependencies read ``request.app.state`` at runtime (engines, redis,
settings, bus, cipher) — set up by the app during lifespan — so core never
imports app (the dependency direction stays app -> core -> shared).
"""

import ipaddress
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request

from core.audit.service import AuditService
from core.auth.access_service import AccessService
from core.auth.security.tokens import decode_access_token
from core.auth.service import AuthService
from core.tenants.service import TenantService
from shared.context import Actor, RequestContext, TenantContext
from shared.endpoint_markers import AUTHENTICATED_ATTR, PERMISSION_ATTR, PUBLIC_ATTR
from shared.errors import AuthenticationError, InvariantViolationError, PermissionDeniedError
from shared.service import SqlAlchemyUnitOfWork, UnitOfWork


@dataclass(slots=True)
class ServiceBundle:
    ctx: TenantContext
    request: RequestContext
    auth: AuthService
    tenants: TenantService
    access: AccessService
    audit: AuditService
    # The request-scoped unit of work, exposed so sibling core modules (billing,
    # ...) can build their services on the SAME transaction via their own deps.
    uow: UnitOfWork

    @property
    def user_id(self) -> UUID:
        if self.ctx.actor.kind != "user" or self.ctx.actor.id is None:
            raise AuthenticationError("authenticated user required")
        return UUID(self.ctx.actor.id)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _valid_ip(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return None  # e.g. TestClient's "testclient" — not storable as INET
    return raw


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        ip=_valid_ip(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
    )


def _context_from_request(request: Request, *, require_auth: bool) -> TenantContext:
    request_id = getattr(request.state, "request_id", None)
    token = _bearer_token(request)
    if token is None:
        if require_auth:
            raise AuthenticationError("missing bearer token")
        return TenantContext(
            tenant_id=None, actor=Actor(kind="system", id=None), request_id=request_id
        )
    claims = decode_access_token(token, request.app.state.settings)
    tenant_raw = claims.get("tenant")
    return TenantContext(
        tenant_id=UUID(tenant_raw) if tenant_raw else None,
        actor=Actor(kind="user", id=claims["sub"]),
        request_id=request_id,
    )


async def _open_bundle(
    request: Request, ctx: TenantContext, *, maintenance: bool
) -> AsyncIterator[ServiceBundle]:
    state = request.app.state
    database = state.db
    factory = database.maintenance_sessions if maintenance else database.user_sessions
    uow = SqlAlchemyUnitOfWork(factory, context=ctx)
    async with uow:
        tenants = TenantService(uow, state.bus, ctx)
        bundle = ServiceBundle(
            ctx=ctx,
            request=_request_context(request),
            auth=AuthService(
                uow,
                state.bus,
                ctx,
                redis=state.redis,
                settings=state.settings,
                cipher=state.cipher,
            ),
            tenants=tenants,
            access=AccessService(tenants),
            audit=AuditService(uow.session, ctx),
            uow=uow,
        )
        yield bundle


# --- request-scoped bundle providers (no marker; provide DB-bound services) ---


async def public_bundle(request: Request) -> AsyncIterator[ServiceBundle]:
    ctx = _context_from_request(request, require_auth=False)
    async for bundle in _open_bundle(request, ctx, maintenance=False):
        yield bundle


async def authed_bundle(request: Request) -> AsyncIterator[ServiceBundle]:
    ctx = _context_from_request(request, require_auth=True)
    async for bundle in _open_bundle(request, ctx, maintenance=False):
        yield bundle


async def maintenance_bundle(request: Request) -> AsyncIterator[ServiceBundle]:
    """Authenticated user + maintenance UoW: create_tenant / accept_invitation."""
    ctx = _context_from_request(request, require_auth=True)
    async for bundle in _open_bundle(request, ctx, maintenance=True):
        yield bundle


def _storefront_context(request: Request) -> TenantContext:
    """Storefront (buyer) context: actor from the JWT, tenant from the shop the
    buyer is browsing (X-Shop-Tenant header) — the buyer is authenticated but NOT
    a member of the shop's tenant (decision OV-39). RLS then scopes reads/writes to
    that shop; the feature's service enforces per-object ownership
    (``customer_user_id == ctx.actor.id``) as the second line."""
    request_id = getattr(request.state, "request_id", None)
    token = _bearer_token(request)
    if token is None:
        raise AuthenticationError("missing bearer token")
    claims = decode_access_token(token, request.app.state.settings)
    shop_raw = request.headers.get("x-shop-tenant")
    if not shop_raw:
        raise InvariantViolationError("X-Shop-Tenant header is required for storefront access")
    try:
        shop = UUID(shop_raw)
    except ValueError as exc:
        raise InvariantViolationError("X-Shop-Tenant must be a tenant UUID") from exc
    return TenantContext(
        tenant_id=shop, actor=Actor(kind="user", id=claims["sub"]), request_id=request_id
    )


async def storefront_bundle(request: Request) -> AsyncIterator[ServiceBundle]:
    """Buyer-scoped bundle: authenticated actor + the shop tenant from the request
    (OV-39). Paired with the ``authenticated_endpoint`` marker on storefront routes."""
    ctx = _storefront_context(request)
    async for bundle in _open_bundle(request, ctx, maintenance=False):
        yield bundle


# --- the three declaration markers (interfaces §5.2) ---


def require_permission(code: str) -> Callable[..., Awaitable[None]]:
    async def dependency(bundle: ServiceBundle = Depends(authed_bundle)) -> None:
        if bundle.ctx.tenant_id is None or bundle.ctx.actor.id is None:
            raise PermissionDeniedError("tenant-scoped access token required")
        await bundle.access.require(UUID(bundle.ctx.actor.id), code)

    setattr(dependency, PERMISSION_ATTR, code)
    return dependency


def authenticated_endpoint(reason: str) -> Callable[..., Awaitable[None]]:
    async def dependency(request: Request) -> None:
        # Validate the JWT only; the endpoint opens whichever bundle it needs.
        _context_from_request(request, require_auth=True)

    setattr(dependency, AUTHENTICATED_ATTR, reason)
    return dependency


def public_endpoint(reason: str) -> Callable[..., None]:
    def dependency() -> None:
        return None

    setattr(dependency, PUBLIC_ATTR, reason)
    return dependency
