from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings


def test_defaults_are_dev_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_env == "dev"
    assert settings.sentry_dsn == ""
    assert settings.cors_origin_list == ()
    assert settings.enabled_module_list == ()


def test_csv_lists_parsed_with_whitespace() -> None:
    settings = Settings(
        _env_file=None,
        cors_origins=" https://shop.uz , https://admin.shop.uz ",
        enabled_modules="commerce, crm",
    )
    assert settings.cors_origin_list == ("https://shop.uz", "https://admin.shop.uz")
    assert settings.enabled_module_list == ("commerce", "crm")


_PROD_SECRETS: dict[str, Any] = {
    "jwt_secret": "x" * 32,
    "secret_encryption_keys": Fernet.generate_key().decode(),
    "password_reset_url": "https://app.example.uz/reset-password#token={token}",
}


def test_env_variables_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CORS_ORIGINS", "https://only.uz")
    for name, value in _PROD_SECRETS.items():
        monkeypatch.setenv(name.upper(), value)
    settings = Settings(_env_file=None)
    assert settings.app_env == "prod"
    assert settings.cors_origin_list == ("https://only.uz",)


@pytest.mark.parametrize(
    ("override", "complaint"),
    [
        ({"jwt_secret": "dev-insecure-change-me"}, "JWT_SECRET"),
        ({"jwt_secret": "too-short"}, "JWT_SECRET"),
        ({"secret_encryption_keys": ""}, "SECRET_ENCRYPTION_KEYS"),
        (
            {"password_reset_url": "http://localhost:3000/reset-password#token={token}"},
            "PASSWORD_RESET_URL",
        ),
    ],
)
def test_prod_refuses_dev_secrets(override: dict[str, Any], complaint: str) -> None:
    with pytest.raises(ValidationError, match=complaint):
        Settings(_env_file=None, app_env="prod", **{**_PROD_SECRETS, **override})


def test_prod_starts_with_real_secrets() -> None:
    assert Settings(_env_file=None, app_env="prod", **_PROD_SECRETS).app_env == "prod"


def test_dev_keeps_insecure_defaults_for_zero_config_start() -> None:
    assert Settings(_env_file=None).jwt_secret == "dev-insecure-change-me"


def test_password_reset_url_must_carry_token_placeholder() -> None:
    with pytest.raises(ValidationError, match="token"):
        Settings(_env_file=None, password_reset_url="https://app.example.uz/reset")
