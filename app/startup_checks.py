"""Startup validation: an endpoint without a permission declaration does not start.

Walks all routes before traffic is accepted (interfaces doc §5.3): every
non-infrastructure route must carry EXACTLY ONE of the three markers —
``require_permission`` / ``authenticated_endpoint`` / ``public_endpoint``
(the dependency factories arrive with core/auth in Phase 2; the marker
protocol lives in shared/endpoint_markers.py). The same walk runs as a CI
test, so a violation turns red before deploy, not in production.

Routes are walked through ``iter_route_contexts`` (the same flattener FastAPI's
OpenAPI generation uses) rather than ``app.routes`` directly: ``include_router``
registers a lazy ``_IncludedRouter`` node, so iterating ``app.routes`` and
filtering on ``isinstance(route, APIRoute)`` would miss every mounted router and
silently validate nothing. The contexts expose the EFFECTIVE dependant, so
router-level and include-level dependencies (not just route-level) are seen.
"""

from collections.abc import Callable, Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from core.admin.registry import ADMIN_PREFIX
from shared.endpoint_markers import ALL_MARKER_ATTRS, PERMISSION_ATTR

INFRA_PATH_WHITELIST = frozenset(
    {
        "/health",
        "/ready",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
    }
)


def _iter_dependency_calls(dependant: Dependant) -> Iterator[Callable[..., Any]]:
    if dependant.call is not None:
        yield dependant.call
    for sub_dependant in dependant.dependencies:
        yield from _iter_dependency_calls(sub_dependant)


def _marker_attrs(dependant: Dependant) -> list[str]:
    """Marker attribute NAMES present on a route's (effective) dependency tree."""
    found: list[str] = []
    for call in _iter_dependency_calls(dependant):
        found.extend(attr for attr in ALL_MARKER_ATTRS if hasattr(call, attr))
    return found


def _api_route_contexts(app: FastAPI) -> Iterator[tuple[RouteContext, Dependant]]:
    """Yield (context, effective dependant) for every APIRoute reachable from the
    app, resolving lazily-included routers."""
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue  # /docs, /openapi.json etc. are plain Starlette routes
        dependant = getattr(context, "dependant", None)
        if dependant is None:
            continue
        yield context, dependant


def validate_route_permissions(app: FastAPI) -> None:
    """Raise RuntimeError (application refuses to start) listing every route
    that does not carry exactly one permission marker."""
    problems: list[str] = []
    for context, dependant in _api_route_contexts(app):
        if context.path in INFRA_PATH_WHITELIST:
            continue
        attrs = _marker_attrs(dependant)
        if len(attrs) != 1:
            methods = ",".join(sorted(context.methods or set()))
            found = ", ".join(attrs) if attrs else "no markers"
            problems.append(f"{methods} {context.path} — {found}")
    if problems:
        raise RuntimeError(
            "Routes without a valid permission declaration "
            "(exactly one of require_permission / authenticated_endpoint / "
            "public_endpoint is required):\n- " + "\n- ".join(problems)
        )


def validate_admin_routes(app: FastAPI) -> None:
    """Stricter rule for admin routes (interfaces §5.4): every route under
    ``/api/admin`` must carry EXACTLY the ``require_permission`` marker — the admin
    surface never uses ``authenticated_endpoint`` or ``public_endpoint``. Runs
    after the screens are mounted; a violation refuses startup and turns CI red."""
    problems: list[str] = []
    for context, dependant in _api_route_contexts(app):
        if not (context.path or "").startswith(ADMIN_PREFIX):
            continue
        attrs = _marker_attrs(dependant)
        if attrs != [PERMISSION_ATTR]:
            methods = ",".join(sorted(context.methods or set()))
            found = ", ".join(attrs) if attrs else "no markers"
            problems.append(f"{methods} {context.path} — {found}")
    if problems:
        raise RuntimeError(
            "Admin routes must carry exactly one require_permission marker "
            "(authenticated_endpoint / public_endpoint are forbidden in admin):\n- "
            + "\n- ".join(problems)
        )
