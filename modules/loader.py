"""Feature loader: discovery, manifest parsing, dependency validation, ordering.

Pure (no FastAPI) so the app composition root (`app/features.py`), the CI
manifest-honesty test and `tools/add-feature` share ONE source of truth about
what a feature is and what it requires. A feature is a self-contained folder
`modules/<module>/<feature>/` with a `feature.toml` manifest (anatomy — master
prompt §АНАТОМИЯ ФИЧИ). Only features of ENABLED modules are discovered.

The manifest is the machine-readable contract of a feature's horizontal links.
The loader enforces it at startup: a feature present on disk whose
`requires_features` are absent refuses to boot with a clear error (e.g. "cart
requires commerce.products, which is not installed"). The same check runs in CI.
"""

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

# core modules are always present (they are the platform); a feature's
# requires_core is validated against this set.
CORE_MODULES = frozenset({"auth", "tenants", "billing", "notifications", "audit", "admin"})

# Repo root = the directory that holds modules/ (this file is modules/loader.py).
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FeatureError(RuntimeError):
    """A feature manifest is malformed or its dependencies are unsatisfiable.
    Raised at startup (and in the CI test) — the application refuses to boot."""


@dataclass(frozen=True, slots=True)
class FeatureManifest:
    name: str  # "<module>.<feature>", e.g. "commerce.products"
    module: str  # "commerce"
    feature: str  # "products"
    path: Path  # the feature directory
    description: str
    requires_features: tuple[str, ...]
    requires_core: tuple[str, ...]
    owns_tables: tuple[str, ...]
    publishes_events: tuple[str, ...]
    listens_events: tuple[str, ...]

    @property
    def package(self) -> str:
        """Importable package path: `modules.<module>.<feature>`."""
        return f"modules.{self.module}.{self.feature}"


def _as_str_tuple(value: object, *, key: str, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise FeatureError(f"feature {name!r}: {key} must be a list of strings")
    return tuple(value)


def read_manifest(feature_dir: Path) -> FeatureManifest:
    """Parse `<feature_dir>/feature.toml`. The `name` must equal
    `<module>.<feature>` derived from the folder layout (single source of truth)."""
    toml_path = feature_dir / "feature.toml"
    if not toml_path.exists():
        raise FeatureError(f"no feature.toml in {feature_dir}")
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))

    module = feature_dir.parent.name
    feature = feature_dir.name
    expected = f"{module}.{feature}"
    name = data.get("name")
    if name != expected:
        raise FeatureError(
            f"feature.toml name {name!r} must equal {expected!r} (derived from the folder path)"
        )
    return FeatureManifest(
        name=expected,
        module=module,
        feature=feature,
        path=feature_dir,
        description=str(data.get("description", "")),
        requires_features=_as_str_tuple(
            data.get("requires_features"), key="requires_features", name=expected
        ),
        requires_core=_as_str_tuple(data.get("requires_core"), key="requires_core", name=expected),
        owns_tables=_as_str_tuple(data.get("owns_tables"), key="owns_tables", name=expected),
        publishes_events=_as_str_tuple(
            data.get("publishes_events"), key="publishes_events", name=expected
        ),
        listens_events=_as_str_tuple(
            data.get("listens_events"), key="listens_events", name=expected
        ),
    )


def discover_manifests(
    enabled_modules: Iterable[str], project_root: Path = PROJECT_ROOT
) -> dict[str, FeatureManifest]:
    """All feature manifests of the enabled modules physically present on disk,
    keyed by feature name. A module is a folder under `modules/`; its features are
    subfolders that contain a `feature.toml`."""
    manifests: dict[str, FeatureManifest] = {}
    for module in enabled_modules:
        module_dir = project_root / "modules" / module
        if not module_dir.is_dir():
            raise FeatureError(
                f"ENABLED_MODULES lists {module!r}, but modules/{module} does not exist"
            )
        for feature_dir in sorted(p for p in module_dir.iterdir() if (p / "feature.toml").exists()):
            manifest = read_manifest(feature_dir)
            manifests[manifest.name] = manifest
    return manifests


def validate_dependencies(manifests: Mapping[str, FeatureManifest]) -> None:
    """Every `requires_features` must be present among the loaded manifests and
    every `requires_core` must name a real core module. Raise FeatureError listing
    all problems — the application refuses to boot."""
    problems: list[str] = []
    for manifest in manifests.values():
        for required in manifest.requires_features:
            if required not in manifests:
                problems.append(
                    f"{manifest.name} requires feature {required!r}, which is not installed"
                )
        for required in manifest.requires_core:
            if required not in CORE_MODULES:
                problems.append(
                    f"{manifest.name} requires core {required!r}, which is not a core module"
                )
    if problems:
        raise FeatureError("Unsatisfied feature dependencies:\n- " + "\n- ".join(problems))


def installation_order(manifests: Mapping[str, FeatureManifest]) -> list[str]:
    """Topological order (a feature installs after its requires). Assumes
    validate_dependencies has passed. Deterministic: ties break alphabetically."""
    remaining = dict(manifests)
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            name
            for name, m in remaining.items()
            if all(dep not in remaining for dep in m.requires_features)
        )
        if not ready:
            cycle = ", ".join(sorted(remaining))
            raise FeatureError(f"cyclic feature dependencies among: {cycle}")
        for name in ready:
            ordered.append(name)
            del remaining[name]
    return ordered
