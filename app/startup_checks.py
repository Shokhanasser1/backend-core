"""Startup validation: an endpoint without a permission declaration does not start.

Walks all routes before traffic is accepted (interfaces doc §5.3): every
non-infrastructure route must carry EXACTLY ONE of the three markers —
``require_permission`` / ``authenticated_endpoint`` / ``public_endpoint``
(the dependency factories arrive with core/auth in Phase 2; the marker
protocol lives in shared/endpoint_markers.py). The same walk runs as a CI
test, so a violation turns red before deploy, not in production.
"""

from collections.abc import Callable, Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from shared.endpoint_markers import ALL_MARKER_ATTRS

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


def route_markers(route: APIRoute) -> list[str]:
    """Marker attributes found on the route's dependency tree (incl. router-level)."""
    found: list[str] = []
    for call in _iter_dependency_calls(route.dependant):
        for attr in ALL_MARKER_ATTRS:
            if hasattr(call, attr):
                found.append(f"{attr}={getattr(call, attr)!r}")
    return found


def validate_route_permissions(app: FastAPI) -> None:
    """Raise RuntimeError (application refuses to start) listing every route
    that does not carry exactly one permission marker."""
    problems: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in INFRA_PATH_WHITELIST:
            continue
        markers = route_markers(route)
        if len(markers) != 1:
            methods = ",".join(sorted(route.methods or set()))
            found = "; ".join(markers) if markers else "no markers"
            problems.append(f"{methods} {route.path} — {found}")
    if problems:
        raise RuntimeError(
            "Routes without a valid permission declaration "
            "(exactly one of require_permission / authenticated_endpoint / "
            "public_endpoint is required):\n- " + "\n- ".join(problems)
        )
