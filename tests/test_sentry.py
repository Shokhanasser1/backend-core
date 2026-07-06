"""Sentry: PII never leaves in an event (threat model §3.4, V6)."""

from typing import Any, cast

from sentry_sdk.types import Event

from app.config import Settings
from app.logging_setup import REDACTED
from app.observability import init_sentry, scrub_sentry_event


def scrub(raw: dict[str, Any]) -> dict[str, Any]:
    result = scrub_sentry_event(cast("Event", raw))
    assert result is not None
    return cast("dict[str, Any]", result)


def test_disabled_without_dsn() -> None:
    assert init_sentry(Settings(_env_file=None, sentry_dsn="")) is False


def test_request_bodies_headers_cookies_stripped() -> None:
    scrubbed = scrub(
        {
            "request": {
                "url": "https://api.example.uz/auth/login",
                "method": "POST",
                "data": {"password": "hunter2"},
                "headers": {"Authorization": "Bearer token"},
                "cookies": "session=abc",
                "query_string": "next=/admin",
                "env": {"REMOTE_ADDR": "1.2.3.4"},
            }
        }
    )
    request = scrubbed["request"]
    assert request["url"] == "https://api.example.uz/auth/login"
    assert request["method"] == "POST"
    for stripped in ("data", "headers", "cookies", "query_string", "env"):
        assert stripped not in request


def test_pii_masked_in_messages() -> None:
    scrubbed = scrub(
        {
            "message": "failed login for jasur@example.uz from +998901234567",
            "logentry": {"message": "notify user@mail.uz please"},
        }
    )
    assert "jasur@example.uz" not in scrubbed["message"]
    assert "+998901234567" not in scrubbed["message"]
    assert "user@mail.uz" not in scrubbed["logentry"]["message"]


def test_extra_sensitive_keys_masked() -> None:
    scrubbed = scrub({"extra": {"refresh_token": "abc", "safe_number": 7}})
    assert scrubbed["extra"]["refresh_token"] == REDACTED
    assert scrubbed["extra"]["safe_number"] == 7
