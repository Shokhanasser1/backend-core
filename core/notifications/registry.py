"""Template catalog (interfaces §3.4): modules register their message templates
at startup, symmetric to ``register_permissions``.

Templates are files in the owning module's ``templates/<locale>/<key>.txt`` (plus
an optional ``<key>.subject.txt`` for email). Registration fails fast if either
mandatory locale (ru, uz) is missing a body file — that IS the parity guarantee,
enforced at startup, double-checked in CI. Rendering uses ``string.Template``
($-substitution): safe for translators (no brace conflicts), never crashes on an
absent variable (required_context is validated by the service at send time).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

from shared.errors import NotFoundError
from shared.i18n import SUPPORTED_LOCALES


@dataclass(frozen=True, slots=True)
class TemplateDef:
    key: str  # "<module>.<purpose>", e.g. "billing.payment_succeeded"
    default_channels: tuple[str, ...]
    required_context: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class RenderedTemplate:
    subject: str | None  # email only
    body: str


class TemplateRegistry:
    def __init__(self) -> None:
        self._defs: dict[str, TemplateDef] = {}
        self._dirs: dict[str, Path] = {}
        self._file_cache: dict[Path, str] = {}

    def register(self, module: str, templates: Sequence[TemplateDef], templates_dir: Path) -> None:
        for tdef in templates:
            if not tdef.key.startswith(f"{module}."):
                raise ValueError(f"template {tdef.key!r} must start with module prefix {module!r}")
            existing = self._defs.get(tdef.key)
            if existing is not None:
                if existing != tdef:
                    raise RuntimeError(f"duplicate template key registered: {tdef.key}")
                continue  # idempotent: same catalog re-registered (a new app instance)
            for locale in SUPPORTED_LOCALES:
                body = templates_dir / locale / f"{tdef.key}.txt"
                if not body.exists():
                    raise RuntimeError(
                        f"template {tdef.key!r} is missing its {locale} body file: {body}"
                    )
            self._defs[tdef.key] = tdef
            self._dirs[tdef.key] = templates_dir

    def get(self, key: str) -> TemplateDef:
        tdef = self._defs.get(key)
        if tdef is None:
            raise NotFoundError(f"unknown notification template: {key}")
        return tdef

    def is_registered(self, key: str) -> bool:
        return key in self._defs

    def all_templates(self) -> Sequence[TemplateDef]:
        return tuple(self._defs.values())

    def dir_for(self, key: str) -> Path:
        return self._dirs[key]

    def render(self, key: str, locale: str, params: Mapping[str, object]) -> RenderedTemplate:
        base = self._dirs[self.get(key).key]
        subst = {k: str(v) for k, v in params.items()}
        body = Template(self._read(base / locale / f"{key}.txt")).safe_substitute(subst)
        subject_path = base / locale / f"{key}.subject.txt"
        subject = (
            Template(self._read(subject_path)).safe_substitute(subst)
            if subject_path.exists()
            else None
        )
        return RenderedTemplate(subject=subject, body=body)

    def _read(self, path: Path) -> str:
        cached = self._file_cache.get(path)
        if cached is None:
            cached = path.read_text(encoding="utf-8")
            self._file_cache[path] = cached
        return cached

    def reset(self) -> None:
        """For tests: clear the catalog between app instances."""
        self._defs.clear()
        self._dirs.clear()
        self._file_cache.clear()


# Process-global catalog; modules register on it at startup.
template_registry = TemplateRegistry()


def register_templates(module: str, templates: Sequence[TemplateDef], templates_dir: Path) -> None:
    """Declared by modules at startup (symmetric to register_permissions)."""
    template_registry.register(module, templates, templates_dir)
