"""crm — third business module (assembled from features by the loader).

A lightweight CRM toolkit on top of the core (auth, tenants). Enabled via
ENABLED_MODULES=crm. Independent of commerce (a client enables whichever modules
it needs). See modules/crm/README.md for the feature map and build recipes.

Features (built one at a time): contacts (people + companies, independent).
deals (pipeline) and tasks (activities) are planned — not built yet, so nothing
here anticipates them.
"""
