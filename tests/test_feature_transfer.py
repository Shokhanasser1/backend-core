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

from modules.loader import (
    CORE_MODULES,
    PROJECT_ROOT,
    FeatureError,
    discover_manifests,
    read_manifest,
    validate_dependencies,
)
from tools.add_feature import add_feature

_COMMERCE = ["commerce"]


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A minimal client project: the core modules features may require."""
    for name in CORE_MODULES:
        (tmp_path / "core" / name).mkdir(parents=True)
    return tmp_path


def _manifest_path(root: Path, feature: str) -> Path:
    module, name = feature.split(".")
    return root / "modules" / module / name / "feature.toml"


def test_transferred_product_stands_alone(target: Path) -> None:
    copied = add_feature("commerce.products", target)
    assert copied == ["commerce.products"]
    assert _manifest_path(target, "commerce.products").exists()
    # The module package marker travels too, or the feature is not importable.
    assert (target / "modules" / "commerce" / "__init__.py").exists()

    manifests = discover_manifests(_COMMERCE, project_root=target)
    assert set(manifests) == {"commerce.products"}
    validate_dependencies(manifests)  # satisfiable -> would boot


def test_add_feature_pulls_the_requires_chain(target: Path) -> None:
    copied = add_feature("commerce.orders", target)
    # orders requires products -> both copied, products first.
    assert copied.index("commerce.products") < copied.index("commerce.orders")
    assert _manifest_path(target, "commerce.products").exists()
    assert _manifest_path(target, "commerce.orders").exists()

    manifests = discover_manifests(_COMMERCE, project_root=target)
    validate_dependencies(manifests)  # the transferred pair is satisfiable


def test_cart_without_products_fails_with_clear_error(target: Path) -> None:
    add_feature("commerce.cart", target)  # copies products + orders + cart
    # Simulate a project that kept cart but dropped products.
    shutil.rmtree(target / "modules" / "commerce" / "products")

    manifests = discover_manifests(_COMMERCE, project_root=target)
    with pytest.raises(FeatureError, match=r"cart requires feature 'commerce\.products'"):
        validate_dependencies(manifests)


def test_add_feature_skips_already_satisfied_dependency(target: Path) -> None:
    # Incremental adds: 'cart' after 'orders' — the shared dependencies are
    # already installed and must be skipped, not re-copied/errored.
    add_feature("commerce.orders", target)  # products + orders
    copied = add_feature("commerce.cart", target)
    assert copied == ["commerce.cart"]  # products + orders satisfied -> skipped

    manifests = discover_manifests(_COMMERCE, project_root=target)
    assert set(manifests) == {"commerce.products", "commerce.cart", "commerce.orders"}
    validate_dependencies(manifests)


def test_add_feature_refuses_to_overwrite(target: Path) -> None:
    add_feature("commerce.products", target)
    with pytest.raises(FeatureError, match="already exists"):
        add_feature("commerce.products", target)
    # --force overwrites cleanly.
    assert add_feature("commerce.products", target, force=True) == ["commerce.products"]


def test_force_overwrites_only_the_requested_feature(target: Path) -> None:
    add_feature("commerce.orders", target)  # products + orders
    local_edit = target / "modules" / "commerce" / "products" / "LOCAL.md"
    local_edit.write_text("client customization", encoding="utf-8")

    copied = add_feature("commerce.orders", target, force=True)

    assert copied == ["commerce.orders"]
    assert local_edit.exists()  # an installed dependency is never wiped by --force


def test_refuses_to_copy_into_the_source_project(target: Path) -> None:
    # A throwaway SOURCE project (never the real repo: before the guard, --force
    # into the source itself deleted the feature it was about to copy).
    source = target
    shutil.copytree(
        PROJECT_ROOT / "modules" / "commerce" / "products",
        source / "modules" / "commerce" / "products",
    )
    shutil.copy(
        PROJECT_ROOT / "modules" / "commerce" / "__init__.py", source / "modules" / "commerce"
    )

    with pytest.raises(FeatureError, match="source"):
        add_feature("commerce.products", source, force=True, source_root=source)
    assert (source / "modules" / "commerce" / "products" / "feature.toml").exists()


def test_refuses_target_without_required_core(tmp_path: Path) -> None:
    (tmp_path / "core" / "auth").mkdir(parents=True)  # tenants missing
    with pytest.raises(FeatureError, match="tenants"):
        add_feature("commerce.products", tmp_path)
    assert not (tmp_path / "modules" / "commerce" / "products").exists()  # nothing copied


def test_malformed_manifest_is_a_feature_error(tmp_path: Path) -> None:
    feature_dir = tmp_path / "modules" / "demo" / "thing"
    feature_dir.mkdir(parents=True)
    (feature_dir / "feature.toml").write_text('name = "demo.thing', encoding="utf-8")
    with pytest.raises(FeatureError, match=r"feature\.toml"):
        read_manifest(feature_dir)
