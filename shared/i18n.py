"""i18n mechanism (Phase 3): locale negotiation + a tiny message catalog.

Content (error strings, notification templates) lives in JSON/text files, never
in code — so translations are reviewable data, not Python literals (and ruff's
ambiguous-unicode checks never fire on Cyrillic message text). This module is
the pure mechanism both the error handler and notification rendering share.

Locale chain across the app: request (Accept-Language) -> user -> tenant -> 'ru'.
Only 'ru' and 'uz' are supported in v1; both are mandatory for every key
(enforced at startup for templates, in CI for everything — parity test).
"""

import json
from pathlib import Path

SUPPORTED_LOCALES: tuple[str, ...] = ("ru", "uz")
DEFAULT_LOCALE = "ru"


def negotiate_locale(*candidates: str | None) -> str:
    """First supported locale among the candidates, else the default. Each
    candidate may be a full tag ('ru-RU'); only the primary subtag matters."""
    for candidate in candidates:
        if not candidate:
            continue
        primary = candidate.split("-", 1)[0].strip().lower()
        if primary in SUPPORTED_LOCALES:
            return primary
    return DEFAULT_LOCALE


def parse_accept_language(header: str | None) -> str | None:
    """Highest-q supported locale from an Accept-Language header, or None.

    Malformed q-values are treated as q=1.0 (lenient); ties keep header order.
    """
    if not header:
        return None
    ranked: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        token, _, params = part.strip().partition(";")
        tag = token.split("-", 1)[0].strip().lower()
        if not tag:
            continue
        quality = 1.0
        if params.startswith("q="):
            try:
                quality = float(params[2:])
            except ValueError:
                quality = 1.0
        ranked.append((quality, -index, tag))
    for _quality, _order, tag in sorted(ranked, reverse=True):
        if tag in SUPPORTED_LOCALES:
            return tag
    return None


class Catalog:
    """key -> {locale -> text}. Missing locale falls back to the default; a
    missing key returns the key itself (so a gap degrades to a stable token,
    never a crash). ``render`` applies ``str.format`` with the given params."""

    def __init__(self, messages: dict[str, dict[str, str]]) -> None:
        self._messages = messages

    def keys(self) -> frozenset[str]:
        return frozenset(self._messages)

    def locales_for(self, key: str) -> frozenset[str]:
        return frozenset(self._messages.get(key, {}))

    def get(self, key: str, locale: str) -> str:
        entry = self._messages.get(key)
        if entry is None:
            return key
        return entry.get(locale) or entry.get(DEFAULT_LOCALE) or key

    def render(self, key: str, locale: str, params: dict[str, object] | None = None) -> str:
        text = self.get(key, locale)
        if not params:
            return text
        try:
            return text.format(**params)
        except (KeyError, IndexError):
            return text  # a template referencing an absent param degrades to raw


def load_locale_catalog(directory: Path) -> Catalog:
    """Build a Catalog from ``<directory>/<locale>.json`` files (one flat
    ``key -> text`` object per locale). Absent files are simply skipped."""
    messages: dict[str, dict[str, str]] = {}
    for locale in SUPPORTED_LOCALES:
        path = directory / f"{locale}.json"
        if not path.exists():
            continue
        data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        for key, text in data.items():
            messages.setdefault(key, {})[locale] = text
    return Catalog(messages)
