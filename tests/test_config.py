import pytest

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


def test_env_variables_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CORS_ORIGINS", "https://only.uz")
    settings = Settings(_env_file=None)
    assert settings.app_env == "prod"
    assert settings.cors_origin_list == ("https://only.uz",)
