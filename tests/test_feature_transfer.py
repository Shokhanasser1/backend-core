"""Constructor acceptance tests (Phase 6): tools/add-feature transfers features.

The two checks the master prompt names:
(a) products transfers into a fresh project and is satisfiable on its own;
(b) cart without products fails validation with a clear error.

Pure (no DB): the loader's discovery + dependency validation are exactly what the
app runs at startup, so validating a transferred tree proves it would boot.
"""

import shutil
from pathlib import Path

import pytest

from modules.loader import FeatureError, discover_manifests, validate_dependencies
from tools.add_feature import add_feature

_COMMERCE = ["commerce"]


def _manifest_path(root: Path, feature: str) -> Path:
    module, name = feature.split(".")
    return root / "modules" / module / name / "feature.toml"


def test_transferred_product_stands_alone(tmp_path: Path) -> None:
    copied = add_feature("commerce.products", tmp_path)
    assert copied == ["commerce.products"]
    assert _manifest_path(tmp_path, "commerce.products").exists()

    manifests = discover_manifests(_COMMERCE, project_root=tmp_path)
    assert set(manifests) == {"commerce.products"}
    validate_dependencies(manifests)  # satisfiable -> would boot


def test_add_feature_pulls_the_requires_chain(tmp_path: Path) -> None:
    copied = add_feature("commerce.orders", tmp_path)
    # orders requires products -> both copied, products first.
    assert copied.index("commerce.products") < copied.index("commerce.orders")
    assert _manifest_path(tmp_path, "commerce.products").exists()
    assert _manifest_path(tmp_path, "commerce.orders").exists()

    manifests = discover_manifests(_COMMERCE, project_root=tmp_path)
    validate_dependencies(manifests)  # the transferred pair is satisfiable


def test_cart_without_products_fails_with_clear_error(tmp_path: Path) -> None:
    add_feature("commerce.cart", tmp_path)  # copies products + cart
    # Simulate a project that kept cart but dropped products.
    shutil.rmtree(tmp_path / "modules" / "commerce" / "products")

    manifests = discover_manifests(_COMMERCE, project_root=tmp_path)
    with pytest.raises(FeatureError, match=r"cart requires feature 'commerce\.products'"):
        validate_dependencies(manifests)


def test_add_feature_skips_already_satisfied_dependency(tmp_path: Path) -> None:
    # Incremental adds: 'cart' after 'orders' both need products — the shared
    # dependency is already installed and must be skipped, not re-copied/errored.
    add_feature("commerce.orders", tmp_path)  # products + orders
    copied = add_feature("commerce.cart", tmp_path)
    assert copied == ["commerce.cart"]  # products satisfied -> skipped

    manifests = discover_manifests(_COMMERCE, project_root=tmp_path)
    assert set(manifests) == {"commerce.products", "commerce.cart", "commerce.orders"}
    validate_dependencies(manifests)


def test_add_feature_refuses_to_overwrite(tmp_path: Path) -> None:
    add_feature("commerce.products", tmp_path)
    with pytest.raises(FeatureError, match="already exists"):
        add_feature("commerce.products", tmp_path)
    # --force overwrites cleanly.
    assert add_feature("commerce.products", tmp_path, force=True) == ["commerce.products"]
