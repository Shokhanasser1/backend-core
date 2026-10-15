"""Discovery of component migration folders.

Feature folders are self-contained (feature.toml anatomy): adding or removing
a component must not require editing any central file — version_locations is
therefore assembled by scanning the tree, not maintained by hand.
"""

import importlib
from pathlib import Path

COMPONENT_MIGRATION_GLOBS = (
    "shared/migrations",
    "core/*/migrations",
    "modules/*/*/migrations",
)
# ORM models of every component, registered on Base.metadata for env.py — the
# same scan-don't-list rule, so a transferred feature's tables are seen too.
COMPONENT_MODEL_GLOBS = (
    "core/*/models.py",
    "modules/*/*/models.py",
)


def discover_version_locations(project_root: Path) -> list[Path]:
    locations: list[Path] = []
    for pattern in COMPONENT_MIGRATION_GLOBS:
        locations.extend(path for path in project_root.glob(pattern) if path.is_dir())
    return sorted(locations)


def register_models(project_root: Path) -> list[str]:
    """Import every component's models module (registers its tables on the
    shared metadata). Returns the imported dotted names."""
    names: list[str] = []
    for pattern in COMPONENT_MODEL_GLOBS:
        for path in sorted(project_root.glob(pattern)):
            dotted = ".".join(path.relative_to(project_root).with_suffix("").parts)
            importlib.import_module(dotted)
            names.append(dotted)
    return names
