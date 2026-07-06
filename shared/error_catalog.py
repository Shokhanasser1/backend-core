"""Localized text for the DomainError hierarchy (Phase 3).

The generic FastAPI handler returns both the machine-readable ``message_key``
and a ``message`` rendered in the request locale via this catalog. Text lives in
``locales/errors/<locale>.json`` (data, not code). ``domain_error_message_keys``
enumerates every key the hierarchy can emit so the parity test can assert both
locales cover all of them.
"""

from pathlib import Path

from shared.errors import DomainError
from shared.i18n import Catalog, load_locale_catalog

_ERRORS_DIR = Path(__file__).parent / "locales" / "errors"

ERROR_CATALOG: Catalog = load_locale_catalog(_ERRORS_DIR)


def domain_error_message_keys() -> frozenset[str]:
    """Every ``message_key`` declared across the DomainError subclass tree."""
    keys: set[str] = {DomainError.message_key}
    stack: list[type[DomainError]] = [DomainError]
    while stack:
        current = stack.pop()
        keys.add(current.message_key)
        stack.extend(current.__subclasses__())
    return frozenset(keys)
