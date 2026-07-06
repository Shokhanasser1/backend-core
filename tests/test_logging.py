"""Logs never contain secrets: denylist masking (threat model V6)."""

import json
from typing import Any

import structlog

from app.logging_setup import REDACTED, configure_logging, mask_sensitive_data


def mask(event: dict[str, Any]) -> dict[str, Any]:
    return dict(mask_sensitive_data(None, "info", event))


def test_top_level_sensitive_keys_masked() -> None:
    masked = mask(
        {
            "event": "login",
            "password": "hunter2",
            "refresh_token": "abc",
            "Authorization": "Bearer xyz",
            "totp_code": "123456",
            "api_key": "k",
        }
    )
    assert masked["event"] == "login"
    for key in ("password", "refresh_token", "Authorization", "totp_code", "api_key"):
        assert masked[key] == REDACTED


def test_nested_and_list_values_masked() -> None:
    masked = mask(
        {
            "event": "webhook",
            "context": {"card_number": "8600...", "amount": 5},
            "items": [{"secret": "s"}, {"ok": 1}],
        }
    )
    assert masked["context"]["card_number"] == REDACTED
    assert masked["context"]["amount"] == 5
    assert masked["items"][0]["secret"] == REDACTED
    assert masked["items"][1]["ok"] == 1


def test_non_sensitive_values_untouched() -> None:
    masked = mask({"event": "x", "tenant_id": "t-1", "count": 3})
    assert masked == {"event": "x", "tenant_id": "t-1", "count": 3}


def test_end_to_end_json_log_is_masked(capfd: "Any") -> None:
    configure_logging("INFO")
    structlog.stdlib.get_logger("test.logger").info("user_login", password="hunter2", user="u-1")
    captured = capfd.readouterr()
    line = (captured.err or captured.out).strip().splitlines()[-1]
    record = json.loads(line)
    assert record["event"] == "user_login"
    assert record["password"] == REDACTED
    assert record["user"] == "u-1"
    assert "hunter2" not in line
