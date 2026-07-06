"""Discovery of component migration folders.

Feature folders are self-contained (feature.toml anatomy): adding or removing
a component must not require editing any central file — version_locations is
therefore assembled by scanning the tree, not maintained by hand.
"""

from pathlib import Path

COMPONENT_MIGRATION_GLOBS = (
    "shared/migrations",
    "core/*/migrations",
    "modules/*/*/migrations",
)


def discover_version_locations(project_root: Path) -> list[Path]:
    locations: list[Path] = []
    for pattern in COMPONENT_MIGRATION_GLOBS:
        locations.extend(path for path in project_root.glob(pattern) if path.is_dir())
    return sorted(locations)
