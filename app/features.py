"""Composition-root wiring for business-module features (Phase 6).

Discovers the features of ENABLED_MODULES via modules/loader.py, validates their
manifest dependencies (a feature present on disk whose requires are absent
refuses startup with a clear error), then installs each in dependency order.

- ``install_modules`` (web): call each feature's ``install()`` and mount its
  ``router``.
- ``install_module_workers`` (arq worker): call ``install()`` only — the worker
  serves no HTTP but needs the features' reliable bus subscribers registered and
  their notification templates in the registry (so the dispatcher can render them).

Lives in app/ because it wires the FastAPI app; the loader itself is pure. Core is
always on and is never a module.
"""

import importlib
from types import ModuleType

from fastapi import FastAPI

from app.config import Settings
from modules.loader import (
    FeatureManifest,
    discover_manifests,
    installation_order,
    validate_dependencies,
)


def _ordered_manifests(settings: Settings) -> list[FeatureManifest]:
    manifests = discover_manifests(settings.enabled_module_list)
    validate_dependencies(manifests)
    return [manifests[name] for name in installation_order(manifests)]


def _install_feature(manifest: FeatureManifest) -> ModuleType:
    package = importlib.import_module(manifest.package)
    installer = getattr(package, "install", None)
    if callable(installer):
        installer()
    return package


def install_modules(app: FastAPI, settings: Settings) -> None:
    for manifest in _ordered_manifests(settings):
        package = _install_feature(manifest)
        router = getattr(package, "router", None)
        if router is not None:
            app.include_router(router)


def install_module_workers(settings: Settings) -> None:
    for manifest in _ordered_manifests(settings):
        _install_feature(manifest)
