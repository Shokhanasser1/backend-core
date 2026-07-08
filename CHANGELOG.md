# Changelog

Формат — [keep a changelog](https://keepachangelog.com/ru/1.1.0/);
версионирование — SemVer (PATCH — фиксы, MINOR — новые возможности без
ручной работы в клиентах, MAJOR — ломающие изменения публичных интерфейсов
ядра или схемы БД). Security-фиксы помечаются `[SECURITY]`.

## [0.8.0] — 2026-07-08 — Stripe-адаптер (бэклог v1.1)

> Третий `PaymentProvider` для зарубежных клиентов (Payme/Click — UZS). Построен
> по отдельной команде владельца. Принято; помечено тегом `v0.8.0`.

### Added

- **`core/billing/adapters/stripe.py` — адаптер Stripe Checkout.** Реализует порт
  `PaymentProvider`. В отличие от Payme/Click (строят URL и принимают merchant-
  колбэки), `create_checkout` делает **серверный** вызов Stripe API
  (`POST /v1/checkout/sessions`, httpx + `call_resilient`: таймаут, повторы на
  5xx/429/сеть, circuit breaker; 4xx — permanent, без повторов) и возвращает
  hosted `url`. Наш `payment_id` едет в `client_reference_id` — вебхук
  привязывается к платежу по нему.
- **Суммы 1:1 с minor units ledger** (у Stripe та же конвенция минимальной единицы
  на валюту) — без `×100` (это quirk только Payme). Валюта — любая (Stripe —
  мультивалютный провайдер); сверяется с суммой в нашей записи.
- **Вебхуки:** подпись `Stripe-Signature` — HMAC-SHA256 над `"{timestamp}.{body}"`
  по секрету эндпоинта (constant-time; несколько `v1` при ротации ключей).
  Маппинг событий: `checkout.session.completed`(paid)→confirm→`mark_succeeded`,
  `checkout.session.expired`→cancel; прочие подписанные события → read-only
  no-op с 200-ack (Stripe шлёт все типы на один эндпоинт и смотрит только на
  HTTP-код). Битая подпись → 400; нераспознанный запрос → 403. Идемпотентность —
  по Stripe event id через существующий `payment_webhooks` ledger; replay-защита
  и элевация тенанта — в `WebhookProcessor` (без изменений).
- **Роут** `POST /api/billing/webhooks/stripe` (public, подпись провайдера).
  Включение — `ENABLED_PAYMENT_PROVIDERS`; провайдер без кредов валит старт.
- **Новые env** (`.env.example`, `DEPLOYMENT.md`): `STRIPE_SECRET_KEY`,
  `STRIPE_WEBHOOK_SECRET`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`. Попутно в
  `.env.example` добавлена отсутствовавшая секция billing (Payme/Click/Stripe).
- **Без новой зависимости:** адаптер рукописный поверх httpx (как Payme/Click),
  SDK Stripe не тянется.

## [0.7.1] — 2026-07-08 — превью/тумбнейлы к product_images

> Продолжение v1.1 по отдельной команде владельца. Принято; помечено тегом `v0.7.1`.

### Added

- **`core/files`: генерация превью.** Порт `ThumbnailPort` + адаптер
  `PillowThumbnailer` (`adapters/pillow.py`): resize по большей стороне (аспект
  сохраняется, апскейла нет), strip EXIF/ICC при перекодировании, кадр GIF → PNG;
  выход остаётся в растровом allowlist. Сборка — `build_thumbnailer`, в `app.state`.
  `FileService.create_thumbnail(source_file_id, *, max_edge=None)` кладёт превью
  как **отдельный** файл (своя строка + ключ), прогоняя байты через тот же
  magic-bytes allowlist. Битую картинку (прошла sniff, но не декодируется) отдаёт
  как 422, не как сбой бэкенда. Настройка `FILES_THUMBNAIL_MAX_EDGE` (256 по умолч.).
- **`commerce.product_images`: превью товара.** Колонка `thumbnail_file_id`
  (nullable, ветка `commerce_product_images0002`); `attach` синхронно генерит
  превью и пишет ссылку (событие `added` несёт `thumbnail_file_id`). Отдача
  `GET /{image_id}/content?size=original|thumb` (`thumb` с фолбэком на оригинал);
  `remove` удаляет и оригинал, и превью. Генерация staff-only и синхронная (за
  портом — при желании выносится в воркер без смены API).
- **Зависимость:** `pillow` (runtime) — растровая обработка превью.

## [0.7.0] — 2026-07-08 — core/files + фича product_images (бэклог v1.1)

> Первый элемент бэклога после V1 (по отдельной команде владельца). Помечен тегом
> `v0.7.0` и запушен в origin.

### Added

- **Модуль ядра `core/files`** — тенантное объектное хранилище. Таблица `files`
  (tenant_id + RLS, ветка Alembic `core_files`): `storage_key`, `content_type`,
  `byte_size`, `checksum_sha256`, `original_filename`. Байты — в бэкенде за портом
  `StoragePort`; в Postgres только метаданные.
  - **Порт + адаптеры:** `FilesystemStorage` (dev/test, без внешнего сервиса) и
    `S3Storage` (прод, boto3 в потоке + `call_resilient`: таймаут/повторы/circuit
    breaker); выбор — `FILES_STORAGE_BACKEND`, сборка — `build_storage` (fail-loud
    при `s3` без кредов, как платёжные провайдеры).
  - **`FileService`** (публичный интерфейс): `upload` (проверка размера +
    magic-bytes allowlist — клиентский Content-Type не доверяется; sha256),
    `get`, `open`, `delete`. События `files.file.uploaded|deleted`.
  - **Права** `files.file:read|upload|delete` (owner/admin — всё, member — read);
    роутер `/api/files` (upload multipart, стрим байт inline, meta, delete).
- **Фича `commerce.product_images`** (requires `commerce.products` + core `files`):
  привязка изображений к товару (staff, RBAC). Таблица `commerce_product_images`
  (ветка `commerce_product_images`; `product_id`/`file_id` — «голые» Uuid без
  межкомпонентных FK, валидируются через `ProductService`/`FileService`). Права
  `commerce.product_image:read|manage`, события `commerce.product_image.added|removed`,
  роутер `/api/commerce/product-images` (attach/list/serve/delete).
- **Зависимости:** `boto3` (S3-адаптер), `python-multipart` (разбор загрузок) —
  runtime; `moto` — dev (мок S3 для теста адаптера).

### Fixed

- Изоляция тестов: `_clean_db` теперь чистит и Redis (рейт-лимитеры/локауты/
  эфемерные токены), а не только Postgres — все запросы TestClient идут с одного IP,
  и per-IP лимит логина (30/60с) иначе протекал между тестами и делал набор
  зависимым от порядка.

### Security

- Загрузки валидируются по magic bytes (заголовку содержимого), а не по
  клиентскому Content-Type; allowlist — только растровые картинки (jpeg/png/webp/
  gif), поэтому SVG/HTML не проходят и inline-отдача XSS-безопасна (глобальный
  `X-Content-Type-Options: nosniff`). Ключи S3 — только из окружения. Загрузка по
  URL не вводится, поэтому SSRF-вектор (модель угроз §2) не оживает.

## [0.6.1] — 2026-07-07 — Проверка боем: фикс add-feature

### Fixed

- `tools/add-feature` больше не падает на общей зависимости при инкрементальной
  сборке: `add-feature commerce.cart` после `commerce.orders` (обе тянут
  `commerce.products`) теперь пропускает уже установленную зависимость вместо
  ошибки «already exists»; явно запрошенная фича по-прежнему не перезаписывается
  без `--force`. Найдено при сборке пилота «магазин с корзиной» из шаблона
  (проверка боем).

## [0.6.0] — 2026-07-07 — Фаза 6: Commerce + механика конструктора (V1 готов)

> Тег `v0.6.0` ставится при приёмке фазы. Завершает V1: ядро + первый модуль +
> механика сборки из фич.

### Added

- **Загрузчик фич** (`modules/loader.py` + `app/features.py`): автодискавери
  фич-папок `ENABLED_MODULES`, парсинг `feature.toml`, валидация `requires` на
  старте с понятной ошибкой, топологический порядок установки. `install_modules`
  (web: install + монтирование роутеров), `install_module_workers` (воркер:
  reliable-подписчики фич + шаблоны уведомлений).
- **Модуль commerce** из трёх фич, собранных через публичные интерфейсы ядра:
  - `commerce.products` — каталог (staff RBAC `commerce.product:*`, события
    `commerce.product.*`, `ProductService.get_sale_info` для соседей);
  - `commerce.cart` (requires products) — storefront-корзина покупателя,
    `commerce.cart.checked_out`;
  - `commerce.orders` (requires products) — заказ → оплата через `PaymentService`
    → reliable-подписчик на `billing.payment.succeeded` (пометка оплачен + чек
    `commerce.order_paid` ru/uz + `commerce.order.paid`) → admin-экран
    `/api/admin/orders` (`commerce.order:read`); отказные ветки отменяют заказ.
- **Buyer-механизм storefront** (ОВ-39): `storefront_bundle` в `core/auth/deps.py`
  — аутентифицированный покупатель (не член тенанта), магазин из заголовка
  `X-Shop-Tenant`, ownership по `customer_user_id` в сервисе.
- **Тест честности манифестов** (`tests/test_manifest_honesty.py`): AST-скан —
  импорты фичи ⊆ `requires_features`, только через публичный пакет соседа.
- **`tools/add-feature`**: копирует фичу и её цепочку `requires` в проект.
- **Приёмочные тесты конструктора** (`tests/test_feature_transfer.py`): products
  переносится и заводится в одиночку; cart без products валит валидацию понятной
  ошибкой.
- **`examples/custom-delivery`** — кейс кастомной фичи (диф, решения, чек-лист);
  README модуля `modules/commerce/README.md` (карта фич + рецепты сборки).

### Changed

- `admin_registry` пересобирается per-app (reset + явная регистрация core-экранов);
  admin-экраны фич регистрирует загрузчик — так меню отражает ровно включённые
  модули. `install_modules` монтируется до `mount_admin_screens`.
- `modules/` в testpaths / mypy / import-linter (слои `app → modules → core →
  shared`) / coverage; `tools/` в mypy / coverage. `conftest.py` перенесён в корень
  (фича-тесты берут фикстуры `commerce_client`/`commerce_payments_client`).
- **coverage `concurrency=["thread","greenlet"]`** — TestClient крутит приложение
  в отдельном потоке; без этого HTTP-исполняемый код считался непокрытым.

### Upgrade notes

- Три новые ветки миграций (`commerce_products`, `commerce_cart`, `commerce_orders`)
  — накат `upgrade heads`. Включение модуля: `ENABLED_MODULES=commerce`.

## [0.5.0] — 2026-07-07 — Фаза 5: CLAUDE.md + документация

> Тег `v0.5.0` ставится при приёмке фазы. Только документация — кода/схемы не
> касается.

### Added / Changed

- `CLAUDE.md` расширен до полного руководства: конвенции именования (таблицы,
  права, события, миграции, ветки Alembic, DB-роли); пошаговые how-to (добавить
  эндпоинт, миграцию, модуль ядра, фичу; точечные рецепты — право, событие+
  подписчик, шаблон уведомления, платёжный провайдер, канал, admin-экран);
  раздел антипаттернов.
- README каждого модуля ядра: `core/{auth,tenants,billing,notifications,audit,
  admin}/README.md` — назначение, публичный интерфейс, события, права, как
  расширять, что не публично (сверено с кодом).
- `docs/DEPLOYMENT.md` — чек-лист прод-развёртывания: провижининг DB-ролей,
  секреты и ротация ключей, миграции, TLS, бэкапы + проверка restore, размещение
  данных в УЗ (закон о ПД), наблюдаемость, воркер/cron/ретенция, обновление
  зависимостей, короткий предполётный чек-лист.
- `README.md` — актуализирован статус (ядро Фазы 0–4 готово), ссылки на CLAUDE.md /
  README модулей / DEPLOYMENT.md.

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
