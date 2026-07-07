"""Admin registry + menu filtering (interfaces §3.6). Pure unit tests — no DB."""

from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter

from core.admin.registry import AdminRegistry, AdminScreen
from core.admin.service import AdminService
from core.auth.access_service import AccessService


def _screen(slug: str, permission: str, module: str = "audit") -> AdminScreen:
    return AdminScreen(
        slug=slug,
        title_key=f"admin.screen.{slug}",
        module=module,
        router=APIRouter(),
        permission=permission,
    )


def test_register_and_screens_sorted_by_slug() -> None:
    registry = AdminRegistry()
    registry.register(_screen("orders", "commerce.order:read"))
    registry.register(_screen("audit", "audit.record:read"))
    assert [s.slug for s in registry.screens()] == ["audit", "orders"]


def test_duplicate_slug_raises() -> None:
    registry = AdminRegistry()
    registry.register(_screen("audit", "audit.record:read"))
    with pytest.raises(RuntimeError, match="duplicate admin screen slug"):
        registry.register(_screen("audit", "something.else:read"))


def test_reregistering_same_screen_is_idempotent() -> None:
    registry = AdminRegistry()
    screen = _screen("audit", "audit.record:read")
    registry.register(screen)
    registry.register(screen)  # same object again — a new app instance in-process
    assert len(registry.screens()) == 1


def test_invalid_slug_raises() -> None:
    registry = AdminRegistry()
    for bad in ("Audit", "/audit", "audit screen", "1audit"):
        with pytest.raises(ValueError, match="invalid admin screen slug"):
            registry.register(_screen(bad, "audit.record:read"))


class _FakeResolver:
    def __init__(self, codes: frozenset[str]) -> None:
        self._codes = codes

    async def get_permission_codes(self, user_id: UUID) -> frozenset[str]:
        return self._codes


async def test_screens_for_returns_only_permitted() -> None:
    registry = AdminRegistry()
    registry.register(_screen("audit", "audit.record:read"))
    registry.register(_screen("billing", "billing.subscription:read", module="billing"))

    # A user who holds only the audit permission sees only the audit screen.
    access = AccessService(_FakeResolver(frozenset({"audit.record:read"})))
    menu = await AdminService(access, registry).screens_for(uuid4())

    assert [m.slug for m in menu] == ["audit"]
    assert menu[0].path == "/api/admin/audit"
    assert menu[0].permission == "audit.record:read"


async def test_screens_for_empty_when_no_permissions() -> None:
    registry = AdminRegistry()
    registry.register(_screen("audit", "audit.record:read"))
    access = AccessService(_FakeResolver(frozenset()))
    assert await AdminService(access, registry).screens_for(uuid4()) == []
