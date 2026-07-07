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
    # app_retention deletes only audit_log rows (schema §3.1); used by the worker's
    # retention sweep, never by the web process.
    database_retention_url: str = (
        "postgresql+asyncpg://app_retention:app_retention@localhost:5432/backend_core"
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
    # processed_events dedup keys matter only over the bus retry horizon (schema §2.7).
    processed_events_retention_days: int = 30

    # --- billing (Phase 3) ---
    # Enabled payment providers, comma-separated (e.g. "payme,click").
    enabled_payment_providers: str = ""
    payment_checkout_ttl_seconds: int = 3600  # abandoned checkout -> expired
    # Auto free/trial subscription on tenant creation (OV-21).
    billing_auto_subscribe: bool = True
    billing_default_plan_code: str = "free"
    # Payme merchant credentials (env; empty = adapter unconfigured).
    payme_merchant_id: str = ""
    payme_merchant_key: str = ""
    # Click credentials.
    click_service_id: str = ""
    click_merchant_id: str = ""
    click_secret_key: str = ""

    # --- notifications (Phase 3) ---
    sms_daily_cap_per_tenant: int = 200  # anti-abuse (OV-25); 0 = unlimited
    notification_retention_days: int = 90
    notification_max_attempts: int = 5
    notification_lease_seconds: int = 300
    # Platform channel config (for tenant_id-less sends: email verification, reset).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@example.uz"
    telegram_bot_token: str = ""
    eskiz_email: str = ""
    eskiz_password: str = ""

    # --- files (object storage; core/files) ---
    # Backend selects the storage adapter: "filesystem" (dev/test default, no
    # external service) or "s3" (S3-compatible, prod). An "s3" backend without
    # credentials fails loudly at startup (build_storage), like payment providers.
    files_storage_backend: Literal["filesystem", "s3"] = "filesystem"
    # Local root for the filesystem backend; empty -> a temp dir. Tests point it
    # at a per-test tmp dir so uploads never leak into the repo.
    files_filesystem_root: str = ""
    files_max_upload_bytes: int = 10 * 1024 * 1024  # 10 MiB cap (threat model)
    # Server-side content-type allowlist (magic bytes are checked, the client
    # Content-Type is never trusted). Raster images only — SVG/HTML stay out so
    # inline serving is XSS-safe.
    files_allowed_content_types: str = "image/jpeg,image/png,image/webp,image/gif"
    files_s3_endpoint_url: str = ""  # empty = AWS default; set for MinIO/other
    files_s3_bucket: str = "backend-core-files"
    files_s3_region: str = "us-east-1"
    files_s3_access_key: str = ""
    files_s3_secret_key: str = ""

    @property
    def files_allowed_content_type_list(self) -> tuple[str, ...]:
        return _split_csv(self.files_allowed_content_types)

    @property
    def enabled_payment_provider_list(self) -> tuple[str, ...]:
        return _split_csv(self.enabled_payment_providers)

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
