"""Permission catalog registry (interfaces doc §5.1).

Permission codes are declared in code (not a table) by each module at startup
via ``register_permissions``. The startup validator (§5.3) refuses to boot if a
route declares a ``require_permission`` code that was never registered, or if
two modules register the same code.

Code format: ``<module>.<resource>:<action>`` — module without the feature name.
"""

import re
from dataclasses import dataclass

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class PermissionDef:
    code: str  # "tenants.member:invite"
    title_key: str  # i18n key for admin UI


class PermissionRegistry:
    def __init__(self) -> None:
        self._by_code: dict[str, PermissionDef] = {}
        self._by_module: dict[str, set[str]] = {}

    def register(self, module: str, permissions: list[PermissionDef]) -> None:
        for perm in permissions:
            if not _CODE_RE.match(perm.code):
                raise ValueError(
                    f"invalid permission code {perm.code!r}: expected "
                    "'<module>.<resource>:<action>'"
                )
            if not perm.code.startswith(f"{module}."):
                raise ValueError(
                    f"permission {perm.code!r} must start with its module prefix {module!r}"
                )
            existing = self._by_code.get(perm.code)
            if existing is not None:
                if existing != perm:
                    raise RuntimeError(f"duplicate permission code registered: {perm.code}")
                continue  # idempotent: same catalog re-registered (e.g. a new app instance)
            self._by_code[perm.code] = perm
            self._by_module.setdefault(module, set()).add(perm.code)

    def is_registered(self, code: str) -> bool:
        return code in self._by_code

    def all_codes(self) -> frozenset[str]:
        return frozenset(self._by_code)

    def require_registered(self, code: str) -> None:
        if code not in self._by_code:
            raise RuntimeError(
                f"permission {code!r} is used by a route but was never registered "
                "via AccessService.register_permissions"
            )

    def reset(self) -> None:
        """For tests: clear the catalog between app instances."""
        self._by_code.clear()
        self._by_module.clear()


# Process-global catalog; modules register on it at startup.
permission_registry = PermissionRegistry()
