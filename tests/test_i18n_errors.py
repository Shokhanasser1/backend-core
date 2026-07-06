"""i18n parity for the DomainError catalog (Phase 3, Task 15).

Mirrors the reference project's messages parity test: every message_key the
error hierarchy can emit must exist in BOTH ru and uz, and the two locales must
carry the exact same key set — no orphan translation in one language.
"""

import json
from pathlib import Path

from shared.error_catalog import ERROR_CATALOG, domain_error_message_keys
from shared.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, negotiate_locale, parse_accept_language

_ERRORS_DIR = Path(__file__).resolve().parent.parent / "shared" / "locales" / "errors"


def _locale_keys(locale: str) -> set[str]:
    data: dict[str, str] = json.loads((_ERRORS_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    return set(data)


def test_every_domain_error_key_is_translated_in_all_locales() -> None:
    for key in domain_error_message_keys():
        assert ERROR_CATALOG.locales_for(key) >= frozenset(SUPPORTED_LOCALES), key


def test_locales_have_identical_key_sets() -> None:
    ru_keys = _locale_keys("ru")
    uz_keys = _locale_keys("uz")
    assert ru_keys == uz_keys, ru_keys ^ uz_keys


def test_no_orphan_catalog_keys_beyond_the_hierarchy() -> None:
    # The catalog must not drift ahead of the code with dead translations.
    assert _locale_keys("ru") == set(domain_error_message_keys())


def test_render_falls_back_and_never_crashes() -> None:
    # Known key resolves per-locale; unknown key degrades to the key itself.
    assert ERROR_CATALOG.get("errors.not_found", "uz") != "errors.not_found"
    assert ERROR_CATALOG.get("errors.does_not_exist", "ru") == "errors.does_not_exist"


def test_locale_negotiation_chain() -> None:
    assert negotiate_locale("uz-UZ", "ru") == "uz"
    assert negotiate_locale(None, "", "en") == DEFAULT_LOCALE
    assert parse_accept_language("en-US,uz;q=0.9,ru;q=0.5") == "uz"
    assert parse_accept_language("en-US,fr;q=0.9") is None
