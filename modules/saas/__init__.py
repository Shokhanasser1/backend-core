"""saas — second business module (assembled from features by the loader).

The SaaS-platform toolkit that sits on top of the core (auth, tenants, billing,
notifications, audit, admin). Enabled via ENABLED_MODULES=saas. See
modules/saas/README.md for the feature map and build recipes.

Features (built one at a time): entitlements (tariff feature flags + limits).
metering (usage) and onboarding (activation checklist) are planned — not built
yet, so nothing here anticipates them.
"""
