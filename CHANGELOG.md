# Changelog

Формат — [keep a changelog](https://keepachangelog.com/ru/1.1.0/);
версионирование — SemVer (PATCH — фиксы, MINOR — новые возможности без
ручной работы в клиентах, MAJOR — ломающие изменения публичных интерфейсов
ядра или схемы БД). Security-фиксы помечаются `[SECURITY]`.

## [0.2.0] — 2026-07-06 — Фаза 2: Auth + Tenants

### Added

- Разделение ролей БД (app_migrator/app_user/app_maintenance/app_retention):
  init-скрипт compose `docker/postgres-init/01-roles.sql` + рендер для тестов;
  рантайм подключается как app_user (RLS применяется), миграции — app_migrator.
- Транзакционно-локальный RLS-контекст (`SET LOCAL app.tenant_id/user_id`,
  fail-closed через NULLIF); хелперы `enable_tenant_rls`; базовая миграция
  `shared0002` (функции `app_current_*`, проверка ролей).
- Таблицы auth (ветка `core_auth`): users, user_totp, user_recovery_codes,
  refresh_tokens (глобальные, ОВ-01).
- Таблицы tenants (ветка `core_tenants`): tenants, memberships, roles,
  role_permissions, invitations с полными RLS-политиками §3.3.
- Таблица `audit_log` (ветка `core_audit`, append-only, ОВ-26) + `AuditService`
  + wildcard-подписчик шины (дедуп по event_id), ретенция 24 мес (ОВ-27).
- Примитивы безопасности: argon2id, JWT HS256 со строгим alg (ОВ-17,
  anti-confusion), TOTP + recovery-коды (ОВ-14), Fernet/MultiFernet (ОВ-19),
  Redis rate limiter / lockout / anti-replay / ephemeral-store.
- AuthService: регистрация, вход, 2FA (TOTP + recovery), refresh с ротацией и
  детектом reuse (ОВ-12), сброс/смена пароля (ОВ-13), logout, tenant-токен.
- TenantService: организации, membership, роли, приглашения, инвариант
  последнего owner. RBAC: реестр прав, системные роли (owner/admin/member) с
  идемпотентным синком на старте, `require_permission`/`authenticated_endpoint`/
  `public_endpoint` с обязательной валидацией на старте.
- Роутеры `/api/auth` и `/api/tenants`; refresh в теле ответа (ОВ-18).
- Конфиг перенесён в `shared/config.py` (слой core→shared). ADR-0010 (JWT).

### Upgrade notes

- Требуются DB-роли: выполнить provisioning (dev — init-скрипт compose)
  до `alembic upgrade heads`. `DATABASE_URL` теперь = app_user; добавлены
  `DATABASE_MIGRATOR_URL`, `DATABASE_MAINTENANCE_URL`, `JWT_SECRET`,
  `SECRET_ENCRYPTION_KEYS` (см. `.env.example`).

## [0.1.0] — 2026-07-06

Фаза 1 — скелет и инфраструктура. Тег `v0.1.0` ставится при приёмке фазы.

### Added

- Структура проекта: `app/` (композиционный корень), `shared/`,
  `migrations/` (env/конфиг), `tests/`, `docs/adr/`.
- shared/: `TenantContext`/`Actor`, иерархия `DomainError` (ОВ-07),
  `Money`, `Page`/`PageResult`, декларативные базы с naming conventions,
  `Repository` с автофильтрацией по tenant_id, `GlobalRepository`,
  `SystemRepository` (только core/), `Service`+`UnitOfWork`
  (события — post-commit), событийная шина (in-process + reliable через
  arq с дедупликацией `processed_events`), маркеры эндпоинтов,
  `TEMPLATE_VERSION`.
- app/: конфиг из окружения (pydantic-settings), JSON-логи structlog с
  маскированием секретов, request_id/security-headers/metrics middleware,
  `/health`, `/ready`, `/metrics` (Prometheus + глубина очереди arq),
  Sentry с PII-scrubbing (выключен без DSN), стартовая валидация
  «эндпоинт без декларации прав не поднимается», arq-воркер.
- Мультиветочный Alembic (ОВ-10): ветка `shared` с миграцией
  `processed_events`; обёртка `python -m migrations.cli` с динамическим
  `version_locations`; `make migrate` = `upgrade heads`.
- Инфраструктура: Docker Compose (api, worker, Postgres 16, Redis 7),
  многостадийный Dockerfile без root, Makefile, pre-commit.
- CI: ruff, mypy strict, import-linter, pytest+coverage (≥85%),
  `uv lock --check`, pip-audit, bandit, gitleaks (ОВ-30), build + trivy;
  заготовка джобы `template-drift` для клиентских проектов.
- ADR-0001…0009; скелет `docs/UPDATE.md`.

### Upgrade notes

- Первый релиз — клиентских проектов ещё нет.

[0.1.0]: — тег будет создан при приёмке Фазы 1
