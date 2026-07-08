# План реализации backend-core

> Статусы: ⬜ не начата · 🟨 в работе · ✅ готова
> Требования и стандарты: `master-prompt-backend-core.md` (v2)

## Видение

Семейство переиспользуемых шаблонов для клиентских проектов рынка
УЗ/СНГ. backend-core — фундамент: ядро (auth, тенанты, платежи,
уведомления, аудит, админ-каркас) + растущая библиотека модулей,
собранных из переносимых фич.

**Commerce — только первый модуль.** Он выбран первым, потому что
прогоняет через себя всё ядро: платежи, уведомления, права, админку.
Следом тем же паттерном (фичи + feature.toml + README-меню) приходят
crm, saas и другие — см. бэклог.

## V1 — ядро + commerce + механика конструктора

| # | Фаза | Что делаем | Критерий приёмки | Статус |
|---|------|-----------|------------------|--------|
| 0 | План и схема | Схема БД ядра; карта интерфейсов и событий; threat model; стратегия обновления шаблона; открытые вопросы | Пользователь утвердил схему | ✅ |
| 1 | Скелет | Структура, Docker Compose, Makefile, uv, конфиг, JSON-логи, health, шина событий, CI со сканами, pre-commit, Sentry, /metrics, ADR №1–6 | `make dev` поднимается; `make test` и CI зелёные | ✅ |
| 2 | Auth + Tenants | Регистрация, JWT+refresh, 2FA, RBAC, организации, приглашения; обязательные права на роутах | Интеграционные тесты всех сценариев, включая негативные | ✅ |
| 3 | Billing + Notifications + i18n | PaymentProvider (Payme, Click), вебхуки с подписью и идемпотентностью; NotificationChannel (Telegram, Eskiz, email) через очередь; каталоги ru/uz | Негативные тесты вебхуков; уведомления уходят из очереди | ✅ |
| 4 | Audit + Admin-каркас | Append-only аудит; admin-API с механизмом регистрации экранов модулей | Стандарты фазы + тесты | 🟨 |
| 5 | CLAUDE.md + документация | Расширить CLAUDE.md конвенциями; README модулей ядра; ADR | Новая сессия Claude работает без этой переписки | 🟨 |
| 6 | Commerce + конструктор | Фичи products/cart/orders; загрузчик фич (feature.toml, проверка requires на старте); tools/add-feature; README-меню модуля | products переносится в чистый проект и заводится; cart без products валит старт с понятной ошибкой | ✅ |

Оценка: ~10–15 рабочих сессий Claude Code.

## После V1 — проверка боем

Собрать первый реальный (или пилотный) клиентский проект из шаблона.
Всё, что при сборке окажется неудобным, вернуть правками в шаблон.
До этой проверки шаблон не полировать.

## Бэклог (строится только по отдельной команде)

| Что | Содержимое | Ориентир |
|-----|-----------|----------|
| ✅ core/files + фича product_images | S3/filesystem-хранилище, magic bytes | v1.1 (тег `v0.7.0`) |
| ✅ Превью/тумбнейлы к product_images | Pillow за портом `ThumbnailPort`, синхронно при attach, `?size=thumb` | продолжение v1.1 (тег `v0.7.1`) |
| ✅ Stripe-адаптер | Третий PaymentProvider для зарубежных клиентов (сервер→сервер checkout, подписанные вебхуки) | v1.1 (тег `v0.8.0`) |
| Модуль saas (в работе) | ✅ `saas.entitlements` (feature flags + лимиты тарифа); далее `metering`, `onboarding` | v2 |
| Модуль crm | Контакты, компании, сделки, воронка, задачи, таймлайн | v2 |
| tg-bot-template | Sibling-шаблон: aiogram поверх API этого же ядра | отдельный репозиторий |
| Идеи будущих модулей | booking (записи: клиники, салоны, курсы), delivery, loyalty | по спросу клиентов |
| Фронтенд админки | Отдельный проект поверх admin-API | по спросу |
| copier-scaffolding | Генерация нового клиентского проекта командой | после решения по стратегии обновления (Фаза 0) |

## Журнал

- 2026-07-08 — **Модуль `saas`, этап 1 — фича `saas.entitlements` ПРИНЯТА,
  закоммичена, тег `v0.9.0`.** Второй бизнес-модуль после commerce (бэклог v2); решения
  Фазы-0-стиля утверждены владельцем: saas — бизнес-модуль из фич (как commerce);
  saas владеет справочником план→лимиты; metering — события→агрегаты (позже);
  onboarding — чек-лист (позже); строим по одной фиче. **Состав модуля** (3
  независимые фичи): `entitlements` (эта), далее `metering`, `onboarding`.
  **`saas.entitlements`** (requires core auth/tenants/billing; полная анатомия
  фичи): права тарифа = feature flags + числовые лимиты. Таблицы —
  `saas_plan_entitlements` (**глобальный** справочник тарифной сетки: `(plan_code,
  entitlement_key)`, `kind∈{flag,limit}`, read-only в рантайме, наполняется
  seed-миграцией клиента) и `saas_tenant_entitlements` (**тенантная**, RLS,
  ветка Alembic `saas_entitlements`: активный `plan_code`+`current_period_end`+
  `canceled`, одна строка на тенант). **`EntitlementService`** (публичный интерфейс
  для соседей): `is_enabled`/`get_limit`/`require_within_limit` (кидает
  `ConflictError` 409)/`snapshot`/`effective_plan_code`. **Подписчики** (reliable,
  как app_user): `billing.subscription.activated`→set active plan,
  `.canceled`→пометка отмены (покрытие держится до `current_period_end` —
  cancel-at-period-end); публикует `saas.entitlement.changed`. Роут
  `GET /api/saas/entitlements/me` (`saas.entitlement:read`, owner/admin). **Дефолт
  (осознанный):** нет активного плана / ключа в сетке → флаг `False`, лимит `None`
  (безлимит) — включение фичи не блокирует тенанта целиком; жёсткий пол = free-план
  billing с явными лимитами. **Решения (чтобы не переоткрывать):** (1) лимиты
  enforce'ит вызывающая бизнес-фича через сервис (передаёт свой `current_count`),
  metering и entitlements независимы; (2) новой `DomainError` НЕ вводил
  (переиспользую `ConflictError`) — тест паритета i18n строго сверяет дерево ошибок,
  а `modules/` грузится лениво → своя ошибка в фиче хрупка. **Обвязка:** фикстура
  `saas_client`; тест reliable-доставки через реальный воркер
  (`tests/test_saas_entitlements_dispatch.py`); README модуля + фичи; `.env.example`
  комментарий `ENABLED_MODULES`. Проверки: **336 тестов зелёные (+7), покрытие
  94.04%**, ruff/mypy strict/import-linter чисто, миграции всех веток обратимы
  (+`saas_entitlements`). Принято, закоммичено, помечено тегом `v0.9.0`. → далее
  этап 2: `saas.metering`.
- 2026-07-08 — **Stripe-адаптер (бэклог v1.1, по команде владельца) — ПРИНЯТ,
  закоммичен, тег `v0.8.0`, запушен в origin.** Третий `PaymentProvider` для
  зарубежных клиентов.
  `core/billing/adapters/stripe.py` реализует порт: `create_checkout` — серверный
  вызов Stripe API (`POST /v1/checkout/sessions`, httpx + `call_resilient`:
  таймаут/повторы на 5xx/429/сеть/circuit breaker; 4xx permanent без повторов),
  наш `payment_id` в `client_reference_id`. **Суммы 1:1** с minor units ledger
  (у Stripe та же конвенция минимальной единицы — без ×100, это quirk Payme);
  валюта любая. **Вебхуки:** подпись `Stripe-Signature` HMAC-SHA256 над
  `"{ts}.{body}"` (constant-time, несколько `v1` при ротации); события
  `checkout.session.completed`(paid)→confirm, `.expired`→cancel, прочие →
  read-only no-op с 200-ack (Stripe шлёт все типы на один эндпоинт, смотрит
  только HTTP-код); битая подпись→400, нераспознанный запрос→403.
  Идемпотентность — по event id через существующий `payment_webhooks` ledger;
  `WebhookProcessor` и порт не менялись (маппинг событий уложен в модель действий
  порта). Роут `POST /api/billing/webhooks/stripe` (public). Wiring:
  `build_payment_providers += stripe`, конфиг `STRIPE_*` (+ отсутствовавшая секция
  billing в `.env.example`), README billing/DEPLOYMENT. **Без SDK Stripe** —
  рукописно поверх httpx (как Payme/Click). Проверки: **329 тестов зелёные,
  покрытие 93.88%**, ruff/mypy strict/import-linter чисто; новый адаптер покрыт
  100%. Найден+исправлен 1 дефект в тестах (route-тесты не тянут `_clean_db` →
  общий `event_id` давал коллизию dedup-ключа с processor-тестом → уникальный id).
  Принято, закоммичено, помечено тегом `v0.8.0`, запушено в origin.
- 2026-07-08 — **`v0.7.1` (превью/тумбнейлы) запушен в origin** (`git push origin
  main` + `git push origin v0.7.1`). `main` = `415072a` синхронен с `origin/main`,
  тег `v0.7.1` на удалёнке. Раньше был отложен по решению владельца — теперь
  опубликован. Кода не трогал.
- 2026-07-08 — **Превью/тумбнейлы к `commerce.product_images` (по команде владельца,
  продолжение v1.1) — приняты, закоммичены, тег `v0.7.1`.** В `core/files` добавлен порт
  `ThumbnailPort` + адаптер `PillowThumbnailer` (resize по большей стороне, strip
  EXIF/ICC, кадр GIF → PNG; выход — в растровом allowlist), сборка `build_thumbnailer`
  в `app.state`. `FileService.create_thumbnail(source_file_id, *, max_edge=None)`
  кладёт превью **отдельным** файлом (своя строка + ключ), прогоняя байты через тот
  же magic-bytes allowlist; битая картинка (прошла sniff, но не декодируется) → 422.
  Фича: колонка `thumbnail_file_id` (ветка `commerce_product_images0002`), `attach`
  синхронно генерит превью (событие `added` несёт `thumbnail_file_id`), отдача
  `GET /{id}/content?size=original|thumb` (`thumb` с фолбэком), `remove` чистит оба
  файла. **Дизайн-решение (утв. владельцем):** генерация синхронная (staff-only,
  низкая нагрузка) — за портом, при желании выносится в воркер без смены API.
  Настройка `FILES_THUMBNAIL_MAX_EDGE` (256). Зависимость `pillow` (runtime).
  Проверки: **305 тестов зелёные, покрытие 93.56%**, ruff/mypy strict/import-linter
  чисто; новый код покрыт (Pillow-адаптер 100%, `create_thumbnail`, сервис фичи 100%).
  Принято, закоммичено, помечено тегом `v0.7.1`.
- 2026-07-08 — **Бэклог v1.1 (по команде владельца): `core/files` + фича
  `commerce.product_images` построены — ждут приёмки.** Модуль ядра `core/files`:
  тенантная таблица `files` (RLS, ветка Alembic `core_files`), порт `StoragePort` +
  адаптеры `filesystem` (dev/test, без внешнего сервиса) и `s3` (boto3 в потоке +
  `call_resilient`: таймаут/повторы/circuit breaker; `build_storage` fail-loud при
  `s3` без кредов). `FileService` (публичный интерфейс): `upload` — валидация
  размера + **magic-bytes allowlist** (клиентский Content-Type не доверяется) +
  sha256; `get`/`open`/`delete`; события `files.file.uploaded|deleted`. Права
  `files.file:read|upload|delete`, роутер `/api/files` (upload multipart, стрим
  inline, meta, delete). Фича `commerce.product_images` (requires `commerce.products`
  + core `files`): привязка картинок к товару (staff RBAC), таблица
  `commerce_product_images` (ветка; `product_id`/`file_id` — «голые» Uuid без
  межкомпонентных FK, валидация через `ProductService`/`FileService`), события
  `commerce.product_image.added|removed`, роутер `/api/commerce/product-images`
  (attach/list/serve/delete). **Wiring:** `app/main` (RBAC + `app.state.file_storage`
  + роутер), `migrations/env` (`import core.files.models`), `loader.CORE_MODULES +=
  files`, `shared/config` + `.env.example` (`FILES_*`), `pyproject` (boto3/
  python-multipart/moto + File/Form в immutable-calls), i18n `errors.storage_error`
  ru/uz. **Попутный фикс изоляции:** `_clean_db` теперь чистит и Redis — per-IP
  лимит логина (30/60с) протекал между тестами (один IP у TestClient) и делал набор
  зависимым от порядка. Проверки: **295 тестов зелёные, покрытие 93.44%**, ruff/mypy
  strict/import-linter (4 слоя) чисто, миграции всех веток обратимы (+`core_files`,
  +`commerce_product_images`). **Сознательно не строил** (доп. объём/зависимость —
  по отдельной команде): генерацию превью/тумбнейлов (нужен Pillow) и пресайн-URL
  (в v1 отдача стримом через приложение). Осталось для приёмки: подтверждение
  владельца; затем коммит + тег.
- 2026-07-07 — **V1 опубликован в origin.** Запушены Фазы 0–6 + проверка боем на
  `github.com/Shokhanasser1/backend-core` (`main` = `1f9babb`, теги `v0.1.0`…`v0.6.1`,
  origin синхронен). Харденинг: добавлен `tests/test_commerce_worker_dispatch.py` —
  реальная reliable-доставка подписчика фичи через воркер (`dispatch_event` резолвит
  и запускает `mark_order_paid`); закрыл пробел, который раньше только симулировался.
  Итог: **279 тестов зелёные, покрытие 93.23%**. Хендофф — `docs/HANDOFF.md`.
- 2026-07-07 — **Проверка боем (после V1):** собран пилот «магазин с корзиной»
  (products+cart+orders) из ядра через `tools/add-feature` (клиент стартовал
  core-only, фичи втянуты цепочкой). Найдено и исправлено **1 трение**: add-feature
  падал на общей зависимости при инкрементальной сборке (`add cart` после
  `add orders`, обе тянут products) → теперь пропускает уже установленную
  зависимость, явно запрошенную фичу без `--force` не перезаписывает (+ регресс-тест).
  Пилот поднят против реального Postgres/Redis: миграции `upgrade heads` +
  сквозной сценарий **товар→корзина→заказ→оплата→чек** прошёл; 15 фича-тестов
  пройдены в клиентском проекте (тесты переезжают с фичей). Конструктор подтверждён
  боем; серьёзных неудобств больше не обнаружено.
- 2026-07-07 — **Фаза 6 завершена — V1 ГОТОВ.** Модуль commerce из трёх фич,
  собранный строго через публичные интерфейсы ядра (приёмочный тест конструктора).
  **cart** (`commerce.cart`, requires products): storefront-корзина покупателя
  (buyer-механизм ОВ-39: `authenticated_endpoint` + магазин из `X-Shop-Tenant` +
  ownership в сервисе; цены через `ProductService`, не читая таблицу); события
  `commerce.cart.checked_out`. **orders** (`commerce.orders`, requires products) —
  сквозной сценарий §6.5: `place_order` оценивает через products, платит через
  `PaymentService`; reliable-подписчик на `billing.payment.succeeded` помечает
  оплаченным + шлёт чек `NotificationService` (шаблон `commerce.order_paid` ru/uz) +
  публикует `commerce.order.paid`; отказные `billing.payment.failed|canceled|expired`
  → отмена; admin-экран `/api/admin/orders` (`commerce.order:read`). **Механика:**
  тест честности манифестов (AST: импорты фичи ⊆ requires_features, только публичный
  пакет), `tools/add-feature` (тянет цепочку requires, копирует папки),
  приёмочные тесты (products в чистый проект заводится; cart без products валит
  валидацию с понятной ошибкой), `examples/custom-delivery` (кейс кастомной фичи),
  README модуля commerce (карта фич + рецепты сборки). Ядро: buyer-механизм
  `storefront_bundle` в core/auth (ОВ-39); admin_registry сбрасывается per-app +
  явная регистрация core-экранов (фича-экраны регистрируются загрузчиком);
  install_module_workers в воркере (reliable-подписчики фич + шаблоны). Конфиг:
  modules/ в testpaths/mypy/coverage/import-linter, tools/ в mypy/coverage.
  Проверки: **275 тестов зелёные, покрытие 92.82%**, ruff/mypy strict/import-linter
  (4 слоя app→modules→core→shared) чисто, миграции всех веток (+3 commerce ветки,
  upgrade heads + downgrade). Осталось: коммит + тег v0.6.0. → далее «проверка боем»
  (первый реальный клиентский проект из шаблона).
- 2026-07-07 — Фаза 6, этап 1 (в работе): механика конструктора + первая фича.
  Решён **ОВ-39 = (б)** (доступ покупателя: `authenticated_endpoint` + ownership в
  сервисе). Построен **загрузчик фич** `modules/loader.py` (автодискавери фич-папок
  ENABLED_MODULES, парсинг `feature.toml`, валидация `requires` на старте с понятной
  ошибкой, топологический порядок установки) + `app/features.py` (`install_modules`:
  импорт, install(), монтирование роутеров). Первая фича **commerce.products** (полная
  анатомия: feature.toml, models с tenant-RLS, schemas, ProductService+repo, permissions
  commerce.product:*, router `/api/commerce/products`, миграция ветки
  `commerce_products`, README, тесты в папке фичи). Инфраструктура фич: `modules/` в
  testpaths/mypy/coverage/import-linter (слои app→modules→core→shared), `conftest.py`
  перенесён в корень (фича-тесты берут `commerce_client` фикстуру, НЕ импортируют app —
  граница слоёв). **Попутно:** coverage `concurrency=["thread","greenlet"]` — TestClient
  крутит приложение в отдельном потоке, без этого HTTP-исполняемый код считался мёртвым
  (покрытие подскочило 88.9%→93.2% по точности). Проверки: **261 тест зелёный, покрытие
  93.16%**, ruff/mypy/import-linter чисто, миграции всех веток (+commerce_products,
  upgrade heads + downgrade). Осталось в Фазе 6: фичи cart+orders (buyer-флоу storefront,
  платежи/уведомления/события/аудит/admin-экран), тест честности манифестов,
  tools/add-feature, приёмочные тесты переноса (products в чистый проект; cart без
  products валит старт), examples/ (кастомная доставка), README модуля commerce.
- 2026-07-07 — Фаза 5 построена (ждёт приёмки владельцем): документация. Расширен
  `CLAUDE.md` — конвенции именования (таблицы/права/события/миграции/ветки/роли),
  пошаговые how-to (эндпоинт, миграция, модуль ядра, фича, точечные рецепты:
  право/событие/шаблон/провайдер/канал/admin-экран) и антипаттерны. README каждого
  модуля ядра (`core/{auth,tenants,billing,notifications,audit,admin}/README.md`):
  назначение, публичный интерфейс, события, права, как расширять — сверено с кодом
  (публичные методы сервисов, реальные имена событий/прав). Чек-лист прод-
  развёртывания `docs/DEPLOYMENT.md`: провижининг DB-ролей, секреты/ротация ключей,
  миграции, TLS, бэкапы+проверка restore, размещение данных в УЗ (ЗРУ о ПД),
  наблюдаемость, воркер/cron/ретенция, обновление зависимостей. Обновлён статус в
  `README.md`. Кода не трогал — только документация; lint/тесты не затронуты.
  Осталось для приёмки: подтверждение владельца; затем коммит + тег v0.5.0.
- 2026-07-07 — Статусы фаз 1–3 выправлены на ✅: таблица отставала (стояли 🟨),
  хотя фазы приняты и помечены тегами v0.1.0/v0.2.0/v0.3.0 (тег ставится при
  приёмке). Фаза 3 закоммичена (ef0a738 на main).
- 2026-07-07 — Фаза 4 построена (ждёт приёмки владельцем): Audit + Admin-каркас.
  **Admin** (`core/admin`, таблиц нет — чистый API, §2.6): `AdminScreen`/
  `AdminRegistry` — модули/фичи регистрируют admin-экраны (симметрично
  `register_permissions`/`register_templates`); монтирование под `/api/admin/{slug}`
  на старте; `GET /api/admin/screens` (право `admin.screen:read`) отдаёт меню только
  из доступных экранов (`AdminService.screens_for`); строгая валидация §5.4 (у
  admin-роута только `require_permission`). **Audit** — достроен: `AuditService.search`
  (фильтры action/actor/object/даты, пагинация, тенант-скоуп RLS + явный фильтр);
  первый admin-экран `audit` (`GET /api/admin/audit`, `audit.record:read`, owner/admin);
  ретенция `audit_log` как `app_retention` (OV-27, 24 мес). **Ретенция служебных
  таблиц** (обещания докстрингов Фазы 4): `processed_events` (§2.7, 30 дн,
  `app_maintenance`) и терминальные PII-строки `notification_outbox` (§2.4,
  `app_maintenance`) — одной суточной джобой `purge_retention`, батчами. Новый конфиг
  `DATABASE_RETENTION_URL`, `PROCESSED_EVENTS_RETENTION_DAYS`. **Попутно исправлены
  два дефекта:** (1) миграция `core_audit0002` — RLS-политики `app_retention` на
  `audit_log` (базовая ревизия выдала грант `SELECT,DELETE`, но политику не создала →
  свип удалял бы 0 строк); (2) стартовая валидация прав переписана на
  `iter_route_contexts` — в текущей версии FastAPI `include_router` создаёт ленивый
  `_IncludedRouter`, и старый обход `app.routes`/`isinstance(APIRoute)` пропускал ВСЕ
  включённые роутеры (инвариант «нет права → не стартует» по факту не применялся).
  Проверки: **247 тестов зелёные**, покрытие **88.88%** (≥85), ruff/mypy strict/
  import-linter чисто, миграции всех веток (upgrade heads + downgrade, включая новую
  core_audit0002). Осталось для приёмки: подтверждение владельца; затем коммит + тег
  v0.4.0.
- 2026-07-07 — Фаза 3 построена (ждёт приёмки владельцем): Billing +
  Notifications + i18n. **Billing:** ветка `core_billing` (валюты/планы
  глобальные; подписки/платежи тенантные без DELETE; вебхуки гибридные),
  `PaymentService` (идемпотентность, статусная машина, активация подписки в
  одной транзакции) + `BillingService`, адаптеры Payme (JSON-RPC/Basic/тийины)
  и Click (md5), `WebhookProcessor` (идемпотентность + элевация system→tenant +
  сверка суммы + диалект провайдера), джоба протухания checkout, авто-подписка
  (ОВ-21), authed `/api/billing` с правами, `docs/RECONCILIATION.md` (ОВ-24).
  **Notifications:** ветка `core_notifications` (settings шифрованы; outbox
  гибридный, SKIP LOCKED + lease, dedup NULLS NOT DISTINCT), `NotificationService`
  (send/get_status/set_channel_config write-only/get_channel_status), реестр
  шаблонов (парити ru/uz на старте), каналы Telegram/Eskiz/SMTP (dormant-by-
  default, circuit breaker, маскирование), диспетчер outbox (arq, backoff,
  dead-letter + `notifications.message.failed`), суточный лимит SMS в Redis
  (ОВ-25), чеки billing→notifications. **i18n:** `shared/i18n.py` + каталог
  ошибок ru/uz в DomainError-хендлере; рендер шаблонов из файлов. `SecretCipher`
  → `shared/encryption.py`; httpx → рантайм. Решены ОВ-20…ОВ-25 (рекомендации).
  Проверки: **219 тестов зелёные**, покрытие 87.78% (≥85%), ruff/mypy strict/
  import-linter чисто, миграции 5 веток (upgrade heads + downgrade), **compose-
  smoke сквозной** (register→tenant→авто-подписка→чек в outbox→диспетчер отправил).
  Осталось для приёмки: подтверждение владельца; затем коммит + тег v0.3.0.
- 2026-07-06 — Фаза 2 построена (ждёт приёмки владельцем): полный цикл
  auth+tenants сверх фундамента. Добавлены минимальный аудит (append-only +
  wildcard-сток), RBAC (реестр прав, системные роли owner/admin/member с
  синком на старте, механика require_permission с обязательной валидацией),
  AuthService (регистрация, вход, 2FA TOTP+recovery, refresh с ротацией и
  детектом reuse, сброс/смена пароля, logout, tenant-токен по ОВ-03),
  TenantService (организации, приглашения, участники, инвариант последнего
  owner), роутеры /api/auth и /api/tenants. ADR-0010 (JWT HS256 + план
  миграции). Проверки: 130 тестов зелёные (сквозные HTTP-потоки + негативные
  V2/V3/V7 + RLS + миграции 4 веток), покрытие 85%, lint/mypy strict/
  import-linter чисто. Осталось для приёмки: самопроверка фазы + подтверждение
  владельца; затем тег и коммит.
- 2026-07-06 — Фаза 2 (фундамент): решены ОВ-11…ОВ-19, ОВ-26, ОВ-27 (все —
  рекомендации). Построен и протестирован фундамент auth/tenants:
  (1) разделение ролей БД (app_migrator/app_user/app_maintenance/
  app_retention), init-скрипт compose + рендер для тестов, транзакционно-
  локальный RLS-контекст (SET LOCAL), хелперы enable_tenant_rls, базовая
  миграция shared0002 (функции app_current_*, проверка ролей);
  (2) таблицы auth (ветка core_auth: users/user_totp/user_recovery_codes/
  refresh_tokens, глобальные) и tenants (ветка core_tenants: tenants/
  memberships/roles/role_permissions/invitations с полными RLS-политиками
  §3.3); (3) примитивы безопасности: argon2id, JWT HS256 со строгим alg
  (anti-confusion), opaque refresh/reset/challenge токены, TOTP+recovery,
  Fernet/MultiFernet шифрование, Redis rate limiter/lockout/anti-replay/
  ephemeral-store. Конфиг перенесён в shared/config.py (слой core→shared).
  Тесты: RLS fail-closed/cross-tenant/pool-leak/WITH-CHECK, миграции трёх
  веток (upgrade heads + downgrade), примитивы безопасности — зелёные.
  Осталось: минимальный аудит (ОВ-26), RBAC-механика require_permission +
  синк системных ролей, AuthService+TenantService+роутеры, сквозные
  интеграционные тесты (все сценарии + негативные), затем самопроверка фазы.
- 2026-07-06 — Фаза 0 утверждена владельцем: решены ОВ-01…ОВ-10 и ОВ-30
  (все — вариант «а»; ОВ-02 — жёсткое удаление, позиция схемы). Решения
  внесены в реестр, исходные документы и мастер-промпт. Блок 1 закрыт.
- 2026-07-06 — Фаза 1 построена (ждёт приёмки владельцем): структура
  app/ + shared/ + migrations/; конфиг из env, JSON-логи с маскированием
  секретов, /health /ready /metrics, Sentry со scrubbing, стартовая
  валидация деклараций прав; шина событий (post-commit, reliable через
  arq + processed_events); Repository/Service/UoW/DomainError; мульти-
  веточный Alembic (ветка shared) с обёрткой migrations/cli; Docker
  Compose + Dockerfile (non-root); Makefile; pre-commit; CI (ruff, mypy
  strict, import-linter, pytest+coverage≥85%, pip-audit, bandit,
  gitleaks, trivy) + заготовка template-drift; ADR-0001…0009; скелет
  docs/UPDATE.md; CHANGELOG 0.1.0. Локально: lint/mypy/import-linter
  зелёные, 86 тестов зелёные, покрытие ~90%, docker-образ собирается.
  Тег v0.1.0 — после приёмки фазы.
- 2026-07-06 — Фаза 0: подготовлены документы проектирования в
  `docs/phase0/` (01 схема БД ядра, 02 интерфейсы и события шины,
  03 модель угроз + ЗРУ-547, 04 стратегия обновления шаблона,
  00 сводка открытых вопросов). Каждый документ прошёл два раунда
  независимой критики и сквозную проверку согласованности. Статус:
  🟨 — ждут утверждения владельцем (открытые вопросы в 00).
- 2026-07-05 — Мастер-промпт переписан в v2: трёхуровневый конструктор,
  feature.toml-манифесты, README-инструкции модулей, scope v1 + бэклог.
  Созданы CLAUDE.md (bootstrap) и PLAN.md. Кода ещё нет.
