"""Configuration from environment only (12-factor). Secrets never live in code:
the repository carries .env.example with fictitious values."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "backend-core"
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # Dev defaults match docker-compose; production values come from the environment.
    database_url: str = "postgresql+asyncpg://backend:backend@localhost:5432/backend_core"
    redis_url: str = "redis://localhost:6379/0"

    sentry_dsn: str = ""  # empty = Sentry disabled
    sentry_traces_sample_rate: float = 0.0

    # Comma-separated lists (plain strings to keep env vars trivial).
    cors_origins: str = ""
    enabled_modules: str = ""

    ready_check_timeout_seconds: float = 2.0

    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        return _split_csv(self.cors_origins)

    @property
    def enabled_module_list(self) -> tuple[str, ...]:
        return _split_csv(self.enabled_modules)


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
