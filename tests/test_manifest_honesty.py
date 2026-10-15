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


def _production_files(feature_dir: Path) -> list[Path]:
    return [
        py
        for py in feature_dir.rglob("*.py")
        if "/tests/" not in py.as_posix() and "/migrations/" not in py.as_posix()
    ]


def _string_args(feature_dir: Path, *, attr: str) -> set[str]:
    """First string-literal argument of every ``<x>.<attr>("...")`` call in the
    feature's production code (``self.emit("a.b.c")``, ``bus.subscribe("a.b.c")``)."""
    found: set[str] = set()
    for py in _production_files(feature_dir):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
    return found


def _model_tables(feature_dir: Path) -> set[str]:
    tables: set[str] = set()
    for py in _production_files(feature_dir):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets)
                and isinstance(node.value, ast.Constant)
            ):
                tables.add(str(node.value.value))
    return tables


def _migration_created_tables(feature_dir: Path) -> set[str]:
    tables: set[str] = set()
    for py in (feature_dir / "migrations").glob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                tables.add(str(node.args[0].value))
    return tables


def test_owns_tables_match_models_and_migrations() -> None:
    problems: list[str] = []
    for manifest in discover_manifests(_all_module_names()).values():
        declared = set(manifest.owns_tables)
        models = _model_tables(manifest.path)
        created = _migration_created_tables(manifest.path) if declared else set()
        if models != declared:
            problems.append(f"{manifest.name}: models {sorted(models)} != owns {sorted(declared)}")
        if created != declared:
            problems.append(
                f"{manifest.name}: migrations create {sorted(created)} != owns {sorted(declared)}"
            )
    assert not problems, "owns_tables dishonesty:\n- " + "\n- ".join(problems)


def test_event_declarations_match_the_code() -> None:
    problems: list[str] = []
    for manifest in discover_manifests(_all_module_names()).values():
        emitted = _string_args(manifest.path, attr="emit")
        subscribed = _string_args(manifest.path, attr="subscribe")
        if emitted != set(manifest.publishes_events):
            problems.append(
                f"{manifest.name}: emits {sorted(emitted)} "
                f"!= publishes_events {sorted(manifest.publishes_events)}"
            )
        if subscribed != set(manifest.listens_events):
            problems.append(
                f"{manifest.name}: subscribes {sorted(subscribed)} "
                f"!= listens_events {sorted(manifest.listens_events)}"
            )
    assert not problems, "event dishonesty:\n- " + "\n- ".join(problems)


def test_features_import_only_declared_core_modules() -> None:
    problems: list[str] = []
    for manifest in discover_manifests(_all_module_names()).values():
        imported = {
            dotted.split(".")[1]
            for dotted in _imports(manifest.path)
            if dotted.startswith("core.") and len(dotted.split(".")) > 1
        }
        undeclared = imported - set(manifest.requires_core)
        if undeclared:
            problems.append(
                f"{manifest.name} imports core {sorted(undeclared)} "
                f"not in requires_core {sorted(manifest.requires_core)}"
            )
    assert not problems, "requires_core dishonesty:\n- " + "\n- ".join(problems)
