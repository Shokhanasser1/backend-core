# Changelog

Формат — [keep a changelog](https://keepachangelog.com/ru/1.1.0/);
версионирование — SemVer (PATCH — фиксы, MINOR — новые возможности без
ручной работы в клиентах, MAJOR — ломающие изменения публичных интерфейсов
ядра или схемы БД). Security-фиксы помечаются `[SECURITY]`.

## [0.4.0] — 2026-07-07 — Фаза 4: Audit + Admin-каркас

> Тег `v0.4.0` ставится при приёмке фазы. Новых таблиц нет — admin это чистый
> API-слой (схема §2.6), audit_log уже создан в Фазе 2.

### Added

- **Admin-каркас** (`core/admin`): `AdminScreen` + `AdminRegistry` — механизм,
  которым модули/фичи регистрируют admin-экраны (симметрично `register_permissions`
  / `register_templates`). Экраны монтируются под `/api/admin/{slug}` на старте;
  `GET /api/admin/screens` (право `admin.screen:read`) возвращает меню — только
  экраны, на которые у пользователя есть право (`AdminService.screens_for`).
  Строгая валидация §5.4: у admin-роута обязан быть ровно `require_permission`
  (`authenticated`/`public` в админке запрещены), проверяется на старте и тестом.
- **Audit — чтение и ретенция** (`core/audit`): `AuditService.search` (фильтры по
  action-префиксу, актору, объекту, диапазону дат; пагинация; тенант-скоуп через
  RLS + явный фильтр). Первый admin-экран — `audit` (`GET /api/admin/audit`,
  право `audit.record:read`, owner/admin). Ретенция `audit_log` джобой воркера
  как `app_retention` (OV-27, дефолт 24 мес).
- **Ретенция служебных таблиц** (закрыты обещания докстрингов «arrives in Phase 4»):
  `processed_events` (ключи дедупликации старше 30 дней, `app_maintenance`, §2.7) и
  терминальные PII-строки `notification_outbox` (старше `notification_retention_days`,
  `app_maintenance`, §2.4 — `recipient` это email/телефон). Все три — одной суточной
  cron-джобой `purge_retention` (03:00), каждый свип ограничен батчами.
- Конфиг: `DATABASE_RETENTION_URL` (подключение `app_retention`, только воркер) и
  `PROCESSED_EVENTS_RETENTION_DAYS`.

### Fixed

- **RLS-политика для `app_retention` на `audit_log`** (миграция `core_audit0002`):
  базовая ревизия выдала роли грант `SELECT, DELETE`, но политики создала только
  для `app_user`/`app_maintenance`. При включённом RLS роль без политики видит ноль
  строк — свип ретенции удалял бы 0 записей. Добавлены `SELECT`/`DELETE`-политики.
- **Валидация деклараций прав охватывала не все роуты.** В текущей версии FastAPI
  `include_router` регистрирует ленивый `_IncludedRouter`, поэтому обход
  `app.routes` с фильтром `isinstance(route, APIRoute)` пропускал все смонтированные
  роутеры — инвариант «эндпоинт без права не стартует» по факту не применялся в
  рантайме. Валидатор переписан на `iter_route_contexts` (тот же обход, что у
  генерации OpenAPI): теперь видны эффективные роуты и их эффективный dependant
  (route-, router- и include-level зависимости). Добавлен тест на пропуск
  незадекларированного включённого роутера.

## [0.3.0] — 2026-07-07 — Фаза 3: Billing + Notifications + i18n

> Тег `v0.3.0` ставится при приёмке фазы.

### Added

- **Billing** (ветка `core_billing`): валюты/планы (глобальные), подписки/платежи
  (тенантные, без DELETE — финансовая история), журнал вебхуков (гибридный).
  `PaymentService` (идемпотентное создание, статусная машина `created→pending→
  succeeded|failed|canceled|expired`, активация подписки в одной транзакции),
  `BillingService` (планы, старт/отмена/авто-подписка). Адаптеры Payme (JSON-RPC,
  Basic-auth, тийины ×100) и Click (md5-подпись); `WebhookProcessor`
  (идемпотентность, элевация system→tenant, сверка суммы, ответ в диалекте
  провайдера). Джоба протухания checkout, авто-подписка новых тенантов (ОВ-21),
  authed-эндпоинты `/api/billing` с правами. `docs/RECONCILIATION.md` (ручная
  сверка, ОВ-24).
- **Notifications** (ветка `core_notifications`): `notification_settings`
  (тенантные, конфиг каналов шифрован), `notification_outbox` (гибридный;
  диспатч `SELECT FOR UPDATE SKIP LOCKED` + lease; dedup `NULLS NOT DISTINCT`).
  `NotificationService`: `send` (Recipient user/address, цепочка локали, dedup),
  `get_status`, `set_channel_config` (write-only, threat model V10),
  `get_channel_status` (маскированный). `register_templates` (симметрично
  `register_permissions`, парити ru/uz на старте).
- **Каналы** (`NotificationChannel`): Telegram (Bot API), Eskiz SMS
  (нормализация телефона, кэш токена + прозрачный re-auth на 401, cap 280),
  email/SMTP; dormant-by-default (нет кредов → no-op). Диспетчер outbox (arq,
  backoff 1→2→4→8→16 мин, dead-letter + `notifications.message.failed`),
  суточный лимит SMS per-tenant в Redis (ОВ-25), circuit breaker на канал
  (`shared/resilience.py`), маскирование адресов в логах/событиях.
- **i18n**: `shared/i18n.py` (negotiate_locale/Accept-Language/Catalog), каталог
  ошибок `shared/locales/errors/{ru,uz}.json` подключён в DomainError-хендлер
  (поле `message`); рендер шаблонов из файлов `templates/<locale>/` (`ru`+`uz`
  обязательны). Чеки об оплате billing→notifications на `billing.payment.succeeded`
  / `billing.subscription.activated` (шаблоны billing, получатель — владелец).
- `SecretCipher` вынесен в `shared/encryption.py` (общий auth+notifications).
  httpx переведён в рантайм-зависимости.

### Upgrade notes

- Новые ветки миграций `core_billing`, `core_notifications` — накат `upgrade heads`.
- Новые env (см. `.env.example`): `ENABLED_PAYMENT_PROVIDERS`, креды Payme/Click,
  `PAYMENT_CHECKOUT_TTL_SECONDS`, `BILLING_*`; `SMS_DAILY_CAP_PER_TENANT`,
  `NOTIFICATION_*`, платформенные `SMTP_*`, `TELEGRAM_BOT_TOKEN`, `ESKIZ_*`.
- Воркер получил cron-джобы: протухание checkout (5 мин) и диспетчер
  уведомлений (15 с).

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
