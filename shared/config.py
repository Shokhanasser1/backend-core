"""Configuration from environment only (12-factor). Secrets never live in code:
the repository carries .env.example with fictitious values.

Lives in shared/ (a cross-cutting primitive) so core services can read config
without importing app — keeping the dependency direction app -> core -> shared.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "backend-core"
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # DB role separation (schema §3.1): runtime connects as app_user (RLS
    # applies), migrations as app_migrator (owner), cross-tenant operations as
    # app_maintenance. Dev defaults match the roles created by the compose
    # init script; production supplies real credentials per role.
    database_url: str = "postgresql+asyncpg://app_user:app_user@localhost:5432/backend_core"
    database_migrator_url: str = (
        "postgresql+asyncpg://app_migrator:app_migrator@localhost:5432/backend_core"
    )
    database_maintenance_url: str = (
        "postgresql+asyncpg://app_maintenance:app_maintenance@localhost:5432/backend_core"
    )

    redis_url: str = "redis://localhost:6379/0"

    sentry_dsn: str = ""  # empty = Sentry disabled
    sentry_traces_sample_rate: float = 0.0

    # Comma-separated lists (plain strings to keep env vars trivial).
    cors_origins: str = ""
    enabled_modules: str = ""

    ready_check_timeout_seconds: float = 2.0

    # --- auth defaults (OV-11): overridable per project via env ---
    jwt_secret: str = "dev-insecure-change-me"  # noqa: S105 - dev default, prod from env
    jwt_algorithm: str = "HS256"  # OV-17; strict allowlist of one (threat model V3)
    access_token_ttl_seconds: int = 600  # 10 minutes
    refresh_token_ttl_seconds: int = 2_592_000  # 30 days
    login_max_failures: int = 5  # lockout threshold (threat model V2)
    login_lockout_seconds: int = 900  # 15 minutes, exponential up to this cap
    two_factor_challenge_ttl_seconds: int = 300
    password_reset_ttl_seconds: int = 3600
    # Fernet key(s) for encrypting tenant/2FA secrets (OV-19). Comma-separated
    # for MultiFernet rotation; empty in dev = a deterministic dev key is used.
    secret_encryption_keys: str = ""

    # Mandatory 2FA for platform admins (OV-15).
    require_platform_admin_2fa: bool = True

    # audit_log retention (OV-27): 24 months default, env-overridable.
    audit_retention_days: int = Field(default=730)

    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        return _split_csv(self.cors_origins)

    @property
    def enabled_module_list(self) -> tuple[str, ...]:
        return _split_csv(self.enabled_modules)

    @property
    def secret_encryption_key_list(self) -> tuple[str, ...]:
        return _split_csv(self.secret_encryption_keys)


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
