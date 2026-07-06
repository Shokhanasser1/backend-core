# Changelog

Формат — [keep a changelog](https://keepachangelog.com/ru/1.1.0/);
версионирование — SemVer (PATCH — фиксы, MINOR — новые возможности без
ручной работы в клиентах, MAJOR — ломающие изменения публичных интерфейсов
ядра или схемы БД). Security-фиксы помечаются `[SECURITY]`.

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
