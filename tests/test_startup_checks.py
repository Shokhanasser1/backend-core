"""'An endpoint without a permission declaration does not start' — startup validation.

This test is the CI twin of the startup walk (interfaces doc §5.3): a
violation turns red in tests before it can reach production.
"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Depends, FastAPI

from app.config import Settings
from app.main import create_app
from app.startup_checks import validate_route_permissions
from shared.endpoint_markers import AUTHENTICATED_ATTR, PERMISSION_ATTR, PUBLIC_ATTR


def make_marker(attr: str, value: str) -> Callable[..., Any]:
    async def dependency() -> None:
        return None

    setattr(dependency, attr, value)
    return dependency


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(_env_file=None))


def test_infra_routes_pass_whitelist(app: FastAPI) -> None:
    validate_route_permissions(app)  # /health, /ready, /metrics, /docs are whitelisted


def test_route_without_marker_fails(app: FastAPI) -> None:
    @app.get("/unmarked")
    async def unmarked() -> None: ...

    with pytest.raises(RuntimeError, match="/unmarked"):
        validate_route_permissions(app)


@pytest.mark.parametrize(
    ("attr", "value"),
    [
        (PERMISSION_ATTR, "tenants.member:invite"),
        (AUTHENTICATED_ATTR, "user-scoped"),
        (PUBLIC_ATTR, "login endpoint"),
    ],
)
def test_route_with_single_marker_passes(app: FastAPI, attr: str, value: str) -> None:
    marker = make_marker(attr, value)

    @app.post("/marked", dependencies=[Depends(marker)])
    async def marked() -> None: ...

    validate_route_permissions(app)


def test_route_with_two_markers_fails(app: FastAPI) -> None:
    first = make_marker(PUBLIC_ATTR, "reason")
    second = make_marker(AUTHENTICATED_ATTR, "reason")

    @app.post("/contradictory", dependencies=[Depends(first), Depends(second)])
    async def contradictory() -> None: ...

    with pytest.raises(RuntimeError, match="/contradictory"):
        validate_route_permissions(app)


def test_router_level_dependency_marker_is_seen(app: FastAPI) -> None:
    from fastapi import APIRouter

    marker = make_marker(PERMISSION_ATTR, "billing.subscription:read")
    router = APIRouter(dependencies=[Depends(marker)])

    @router.get("/via-router")
    async def via_router() -> None: ...

    app.include_router(router)
    validate_route_permissions(app)
