"""Alembic's target metadata sees every table: core AND feature models (so
autogenerate/compare never mistakes a feature table for an unknown one)."""

from migrations.discovery import register_models
from modules.loader import PROJECT_ROOT, discover_manifests
from shared.db import Base


def test_metadata_covers_every_feature_table() -> None:
    register_models(PROJECT_ROOT)
    modules = [p.name for p in (PROJECT_ROOT / "modules").iterdir() if p.is_dir()]
    manifests = discover_manifests(
        [m for m in modules if any((PROJECT_ROOT / "modules" / m).glob("*/feature.toml"))]
    )
    owned = {table for m in manifests.values() for table in m.owns_tables}

    assert owned  # sanity: features exist
    assert owned <= set(Base.metadata.tables), owned - set(Base.metadata.tables)
    assert {"users", "tenants", "payments", "notification_outbox"} <= set(Base.metadata.tables)
