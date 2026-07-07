"""Manifest honesty (Phase 6): a feature imports only features it declares.

A static AST scan of every feature's production code. It complements the
import-linter layer contract (which permits any modules -> modules import) with
the finer feature -> feature rule that only ``feature.toml`` can express:

1. every imported sibling feature must be listed in ``requires_features``;
2. a sibling is imported through its PUBLIC package (``modules.<m>.<f>``), never a
   deep submodule (§1.2 — the public interface is the package __init__).

A manifest that lies turns this test red — exactly the CI guarantee the anatomy
promises.
"""

import ast
from pathlib import Path

from modules.loader import PROJECT_ROOT, FeatureManifest, discover_manifests


def _all_module_names() -> list[str]:
    root = PROJECT_ROOT / "modules"
    return [
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and any((sub / "feature.toml").exists() for sub in path.iterdir() if sub.is_dir())
    ]


def _imports(feature_dir: Path) -> list[str]:
    """Dotted names imported by the feature's production code (skips tests/migrations,
    which may reach for siblings/app/the test harness freely)."""
    names: list[str] = []
    for py in feature_dir.rglob("*.py"):
        posix = py.as_posix()
        if "/tests/" in posix or "/migrations/" in posix:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=posix)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.append(node.module)
    return names


def _feature_of(dotted: str) -> tuple[str, bool] | None:
    """('<module>.<feature>', is_public_package) for an import of another feature,
    else None. is_public_package is False for a deep submodule import."""
    parts = dotted.split(".")
    if len(parts) < 3 or parts[0] != "modules":
        return None
    return f"{parts[1]}.{parts[2]}", len(parts) == 3


def test_features_import_only_declared_features() -> None:
    manifests = discover_manifests(_all_module_names())
    assert manifests, "no features discovered"
    problems: list[str] = []
    for manifest in manifests.values():
        declared = set(manifest.requires_features)
        for dotted in _imports(manifest.path):
            resolved = _feature_of(dotted)
            if resolved is None:
                continue
            sibling, is_public = resolved
            if sibling == manifest.name:
                continue  # self-import
            if sibling not in declared:
                problems.append(
                    f"{manifest.name} imports {sibling!r} (via {dotted!r}) "
                    f"not in requires_features {sorted(declared)}"
                )
            elif not is_public:
                problems.append(
                    f"{manifest.name} imports {sibling!r} through a private submodule "
                    f"({dotted!r}); use the feature's public package"
                )
    assert not problems, "manifest dishonesty:\n- " + "\n- ".join(problems)


def test_declared_tables_have_a_migration() -> None:
    # A feature's owns_tables must be creatable — its migrations folder must exist.
    manifests: dict[str, FeatureManifest] = discover_manifests(_all_module_names())
    for manifest in manifests.values():
        if manifest.owns_tables:
            assert (manifest.path / "migrations").is_dir(), (
                f"{manifest.name} declares owns_tables but has no migrations/ folder"
            )
