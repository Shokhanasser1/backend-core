"""tools/add-feature: copy a feature (and its requires chain) into a client project.

Reads the source repo's feature manifests, resolves the transitive
``requires_features`` closure, and copies each feature folder to
``<target>/modules/<module>/<feature>``. Refuses to overwrite unless ``--force``.
Prints the chain and the two remaining manual steps (enable the module, run
migrations) — the anatomy promise that a feature transfers by copying its folder.

    python -m tools.add_feature commerce.orders /path/to/target-project
    python -m tools.add_feature commerce.cart /path/to/target-project --force
"""

import argparse
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from modules.loader import (
    PROJECT_ROOT,
    FeatureError,
    FeatureManifest,
    discover_manifests,
)

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def _module_names(source_root: Path) -> list[str]:
    root = source_root / "modules"
    return [
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and any((sub / "feature.toml").exists() for sub in path.iterdir() if sub.is_dir())
    ]


def _resolve_chain(feature: str, manifests: Mapping[str, FeatureManifest]) -> list[str]:
    """Transitive requires closure, dependencies before dependents."""
    if feature not in manifests:
        raise FeatureError(f"unknown feature {feature!r} in the source project")
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        for dep in manifests[name].requires_features:
            if dep not in manifests:
                raise FeatureError(f"{name} requires {dep!r}, which is missing from the source")
            visit(dep)
        order.append(name)

    visit(feature)
    return order


def add_feature(
    feature: str, target_root: Path, *, force: bool = False, source_root: Path = PROJECT_ROOT
) -> list[str]:
    """Copy ``feature`` and its requires chain into ``target_root``. Returns the
    copied feature names in install order."""
    manifests = discover_manifests(_module_names(source_root), project_root=source_root)
    chain = _resolve_chain(feature, manifests)
    for name in chain:
        manifest = manifests[name]
        dest = target_root / "modules" / manifest.module / manifest.feature
        if dest.exists():
            if not force:
                raise FeatureError(f"{dest} already exists (use --force to overwrite)")
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(manifest.path, dest, ignore=_IGNORE)
    return chain


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="add-feature", description="Copy a feature and its requires into a client project."
    )
    parser.add_argument("feature", help="feature name, e.g. commerce.orders")
    parser.add_argument("target", type=Path, help="target project root")
    parser.add_argument("--force", action="store_true", help="overwrite existing feature folders")
    args = parser.parse_args(argv)
    try:
        chain = add_feature(args.feature, args.target, force=args.force)
    except FeatureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    module = args.feature.split(".")[0]
    print("Copied features (install order):")
    for name in chain:
        print(f"  - {name}")
    print("\nNext steps in the target project:")
    print(f"  1. add '{module}' to ENABLED_MODULES")
    print("  2. run: python -m migrations.cli upgrade heads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
