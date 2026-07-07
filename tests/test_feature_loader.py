"""Feature loader: manifest parsing, dependency validation, install order.

Pure unit tests over synthetic feature trees in tmp_path (no DB, no app), plus a
check that the real commerce manifests on disk are valid and satisfiable.
"""

from pathlib import Path

import pytest

from modules.loader import (
    FeatureError,
    discover_manifests,
    installation_order,
    read_manifest,
    validate_dependencies,
)


def _write_feature(
    root: Path,
    module: str,
    feature: str,
    *,
    requires_features: tuple[str, ...] = (),
    requires_core: tuple[str, ...] = ("auth",),
) -> Path:
    directory = root / "modules" / module / feature
    directory.mkdir(parents=True)
    rf = ", ".join(f'"{x}"' for x in requires_features)
    rc = ", ".join(f'"{x}"' for x in requires_core)
    (directory / "feature.toml").write_text(
        f'name = "{module}.{feature}"\n'
        'description = "x"\n'
        f"requires_features = [{rf}]\n"
        f"requires_core = [{rc}]\n"
        "owns_tables = []\n"
        "publishes_events = []\n"
        "listens_events = []\n",
        encoding="utf-8",
    )
    return directory


def test_read_manifest(tmp_path: Path) -> None:
    directory = _write_feature(tmp_path, "commerce", "products")
    manifest = read_manifest(directory)
    assert manifest.name == "commerce.products"
    assert manifest.module == "commerce"
    assert manifest.feature == "products"
    assert manifest.requires_core == ("auth",)
    assert manifest.package == "modules.commerce.products"


def test_name_must_match_folder(tmp_path: Path) -> None:
    directory = tmp_path / "modules" / "commerce" / "products"
    directory.mkdir(parents=True)
    (directory / "feature.toml").write_text('name = "commerce.wrong"\n', encoding="utf-8")
    with pytest.raises(FeatureError, match="must equal"):
        read_manifest(directory)


def test_discover_only_enabled_modules(tmp_path: Path) -> None:
    _write_feature(tmp_path, "commerce", "products")
    _write_feature(tmp_path, "crm", "contacts")
    manifests = discover_manifests(["commerce"], project_root=tmp_path)
    assert set(manifests) == {"commerce.products"}


def test_enabled_module_without_folder_raises(tmp_path: Path) -> None:
    (tmp_path / "modules").mkdir()
    with pytest.raises(FeatureError, match="does not exist"):
        discover_manifests(["ghost"], project_root=tmp_path)


def test_validate_missing_required_feature(tmp_path: Path) -> None:
    # cart present, products absent -> clear startup error (acceptance check b).
    _write_feature(tmp_path, "commerce", "cart", requires_features=("commerce.products",))
    manifests = discover_manifests(["commerce"], project_root=tmp_path)
    with pytest.raises(FeatureError, match=r"requires feature 'commerce\.products'"):
        validate_dependencies(manifests)


def test_validate_unknown_core(tmp_path: Path) -> None:
    _write_feature(tmp_path, "commerce", "products", requires_core=("nope",))
    manifests = discover_manifests(["commerce"], project_root=tmp_path)
    with pytest.raises(FeatureError, match="not a core module"):
        validate_dependencies(manifests)


def test_installation_order_respects_requires(tmp_path: Path) -> None:
    _write_feature(tmp_path, "commerce", "products")
    _write_feature(tmp_path, "commerce", "cart", requires_features=("commerce.products",))
    _write_feature(tmp_path, "commerce", "orders", requires_features=("commerce.products",))
    manifests = discover_manifests(["commerce"], project_root=tmp_path)
    validate_dependencies(manifests)
    order = installation_order(manifests)
    assert order.index("commerce.products") < order.index("commerce.cart")
    assert order.index("commerce.products") < order.index("commerce.orders")


def test_cycle_is_detected(tmp_path: Path) -> None:
    _write_feature(tmp_path, "commerce", "a", requires_features=("commerce.b",))
    _write_feature(tmp_path, "commerce", "b", requires_features=("commerce.a",))
    manifests = discover_manifests(["commerce"], project_root=tmp_path)
    with pytest.raises(FeatureError, match="cyclic"):
        installation_order(manifests)


def test_real_commerce_manifests_are_valid() -> None:
    # The actual feature folders on disk must parse and be satisfiable.
    manifests = discover_manifests(["commerce"])
    assert "commerce.products" in manifests
    validate_dependencies(manifests)  # must not raise
