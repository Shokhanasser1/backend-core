# HANDOFF — состояние проекта после сессии 2026-07-08

Хендофф для следующей сессии/владельца. Источники правды остаются:
`master-prompt-backend-core.md` (требования), `PLAN.md` (фазы + журнал),
`CLAUDE.md` (конвенции разработки), `docs/phase0/00-open-questions.md` (реестр
решений ОВ).

## ⚑ Актуальное состояние и следующий шаг (читать первым)

- **Голова:** последний тег **`v0.10.0`** — фича `saas.metering` ПРИНЯТА,
  закоммичена, запушена в origin. Рабочее дерево чистое. **342 теста зелёные (+6),
  покрытие 94.20%**, ruff/mypy strict (161 файл)/import-linter чисто, миграции всех
  веток обратимы (+`saas_metering`).
- **Готово:** V1 (теги `v0.1.0`…`v0.6.1`) + бэклог v1.1 (`v0.7.0`/`v0.7.1`/`v0.8.0`)
  + `saas.entitlements` (`v0.9.0`).
- **СЛЕДУЮЩИЙ ШАГ:** приёмка `saas.metering`, затем **этап 3 модуля `saas` —
  фича `saas.onboarding`** (чек-лист активации тенанта; данные, обновляемые по
  событиям). Детали и утверждённые решения этапов 1–2 — в разделах ниже.

## Модуль saas → этап 2: saas.metering (ПРИНЯТ, тег v0.10.0, запушен)

Учёт потребления (usage). Решения Фаза-0-стиля **утверждены владельцем** (с учётом
механики шины): **(1) источник — публичный `MeteringService.record()`, НЕ подписки
на шину** (фичам запрещён wildcard — `shared/events.py`, `WILDCARD_ALLOWED_TOP_PACKAGE
= "core"`; `handler_id`=module+qualname уникален → generic-подписка хрупка и зашивала
бы чужие имена событий → вызывающий код метит явно); **(2) metering и entitlements
НЕЗАВИСИМЫ** (лимиты-счётчики остаются в entitlements через `current_count`).

- **`saas.metering`** (`modules/saas/metering/`, requires core auth/tenants; НЕ
  billing) — счётчики по метрикам, агрегаты по дням.
  - **Таблица** `saas_usage_counters` (тенантная RLS, ветка `saas_metering`): одна
    строка на `(tenant, metric_key, bucket-день)`, `value` bigint, атомарный UPSERT
    (`ON CONFLICT DO UPDATE value=value+delta` — без read-modify-write гонки).
  - **`MeteringService`:** `record(metric, delta, at=)` (в транзакции вызывающего;
    effectively-once, если звать из reliable-обработчика), `usage`/`summary` (окно
    дней). Право `saas.usage:read` (owner/admin), роут `GET /api/saas/usage/me`.
  - **Ретенция:** `purge_expired_usage` (app_maintenance, батчами) вписана в
    воркерную `purge_retention` **условно** — `if "saas" in enabled_module_list`
    (ленивый импорт; app→modules легально; no-op при выключенном saas). Конфиг
    `SAAS_USAGE_RETENTION_DAYS` (400).
  - Событий не публикует/не слушает (чистый примитив, `publishes/listens = []`).
- **Замечание по прогону (на будущее):** НЕ запускать два testcontainers-прогона
  параллельно (полный + отдельный) — конкуренция за ресурсы/соединения подвешивает
  набор (наблюдалось: полный прогон завис на ~90% при 0 CPU, пока рядом шёл
  `test_migrations`). Чистый последовательный перезапуск прошёл (342/94.20%).

## Модуль saas → этап 1: saas.entitlements (ПРИНЯТ, тег v0.9.0, запушен)

## Модуль saas → этап 1: saas.entitlements (ПРИНЯТ, тег v0.9.0, запушен)

Решения Фаза-0-стиля **утверждены владельцем**: saas — бизнес-модуль из фич (как
commerce); saas владеет справочником план→лимиты; metering = события→агрегаты в БД
(позже); onboarding = чек-лист (позже); строим по одной фиче с приёмкой. Состав
модуля — **3 независимые фичи**: `entitlements` (построена), `metering`,
`onboarding` (далее).

- **`saas.entitlements`** (`modules/saas/entitlements/`, requires core
  auth/tenants/billing) — права тарифа = feature flags + числовые лимиты.
  - **Таблицы:** `saas_plan_entitlements` — **глобальный** справочник тарифной
    сетки (`(plan_code, entitlement_key)`, `kind∈{flag,limit}`,
    `bool_value`/`int_value`); read-only в рантайме (GRANT SELECT), наполняется
    seed-миграцией клиента; `plan_code` — «голый» код плана billing без FK.
    `saas_tenant_entitlements` — **тенантная** (RLS, ветка `saas_entitlements`):
    активный `plan_code`+`current_period_end`+`canceled`, одна строка на тенант.
  - **`EntitlementService`** (публичный интерфейс): `is_enabled`/`get_limit`/
    `require_within_limit` (409 `ConflictError`)/`snapshot`/`effective_plan_code`.
  - **Подписчики** (reliable, app_user): `billing.subscription.activated`→активный
    план; `.canceled`→пометка отмены (покрытие до конца периода). Публикует
    `saas.entitlement.changed`. Роут `GET /api/saas/entitlements/me`
    (`saas.entitlement:read`, owner/admin).
- **Решения (чтобы не переоткрывать):**
  - **Лимиты enforce'ит вызывающая бизнес-фича** через сервис (передаёт свой
    `current_count`). **metering и entitlements независимы** (не связаны requires).
    Если позже нужны «лимиты на потребление за период» —
    `require_within_limit` для таких ключей читал бы значение из metering (тогда
    `entitlements requires metering`). По умолчанию — независимо.
  - **Дефолт:** нет активного плана / ключа в сетке → флаг `False`, лимит `None`
    (безлимит). Включение фичи не блокирует тенанта; жёсткий пол = free-план billing
    с явными лимитами.
  - **Новой `DomainError` НЕ вводить** — переиспользован `ConflictError`. Тест
    паритета i18n (`tests/test_i18n_errors.py`) строго сверяет дерево `DomainError`
    с ключами ru/uz, а `modules/` грузится лениво → своя ошибка в фиче хрупка
    (её ключ то есть, то нет в дереве в зависимости от порядка тестов). Новая
    типизированная ошибка — только в `shared`/`core` (всегда импортированы).
  - **Тесты через отдельный engine на вызов:** прямые сервис-хелперы в фича-тесте
    создают/уничтожают свой engine внутри каждого `asyncio.run` — переиспользовать
    один engine между `asyncio.run` нельзя (пул asyncpg привяжется к закрытому
    циклу). Шина enqueue глушится (`bus.bind_enqueue` no-op), иначе cross-loop
    публикация `saas.entitlement.changed` шумит.

## Обновление 2026-07-08 (3) — Stripe-адаптер (принято, тег `v0.8.0`, запушено)

Третий элемент v1.1 (по отдельной команде владельца). **Принято, закоммичено,
тег `v0.8.0`, запушено в origin.** Третий `PaymentProvider` — для зарубежных
клиентов (Payme/Click остаются по UZS):

- **`core/billing/adapters/stripe.py`:** реализует порт. `create_checkout` —
  **серверный** вызов Stripe API (`POST /v1/checkout/sessions`, httpx +
  `call_resilient`: таймаут/повторы 5xx-429-сеть/circuit breaker; 4xx permanent
  без повторов); `payment_id` в `client_reference_id`. **Суммы 1:1** с minor units
  ledger (у Stripe та же конвенция минимальной единицы — без ×100, это quirk
  Payme); валюта любая. **Вебхуки:** `Stripe-Signature` = HMAC-SHA256 над
  `"{ts}.{body}"` (constant-time, несколько `v1` при ротации). События:
  `checkout.session.completed`(paid)→confirm, `.expired`→cancel, прочие →
  read-only no-op с 200-ack (Stripe шлёт все типы на один эндпоинт, смотрит
  только HTTP-код); битая подпись→400, нераспознанное→403.
- **`WebhookProcessor` и порт НЕ менялись** — маппинг событий уложен в имеющуюся
  модель действий; идемпотентность — по Stripe event id через `payment_webhooks`.
- **Роут** `POST /api/billing/webhooks/stripe` (public). Конфиг `STRIPE_*`
  (+ добавлена отсутствовавшая секция billing в `.env.example`). **Без SDK Stripe**
  — рукописно поверх httpx (как Payme/Click).
- **Дефект в тестах (исправлен):** route-тесты не тянут `_clean_db` → общий
  `event_id` давал коллизию dedup-ключа с processor-тестом → уникальный `event_id`.
- Проверки: **329 тестов зелёные, 93.88%**, ruff/mypy strict/import-linter чисто;
  адаптер покрыт 100%. **Статус:** тег `v0.8.0`, запушен в origin.

## Обновление 2026-07-08 (2) — превью/тумбнейлы к product_images (принято, тег `v0.7.1`, запушено)

Продолжение v1.1 по отдельной команде владельца. **Принято, закоммичено и
запушено в origin, тег `v0.7.1`:**

- **`core/files`:** порт `ThumbnailPort` + адаптер `PillowThumbnailer`
  (`adapters/pillow.py`): resize по большей стороне (аспект сохраняется, без
  апскейла), strip EXIF/ICC при перекодировании, кадр GIF → PNG; выход остаётся в
  растровом allowlist. Сборка — `build_thumbnailer`, лежит в `app.state`.
  `FileService.create_thumbnail(source_file_id, *, max_edge=None)` кладёт превью
  **отдельным** файлом (своя строка + ключ) через тот же `upload` (magic-bytes
  allowlist — defence in depth). Битая картинка (прошла sniff, не декодируется) → 422.
- **`commerce.product_images`:** колонка `thumbnail_file_id` (ветка
  `commerce_product_images0002`); `attach` **синхронно** генерит превью (событие
  `added` несёт `thumbnail_file_id`), отдача `GET /{id}/content?size=original|thumb`
  (`thumb` с фолбэком на оригинал), `remove` чистит оба файла.
- **Дизайн-решение (утв. владельцем):** синхронная генерация (staff-only, низкая
  нагрузка) — за портом, при желании выносится в воркер без смены API. Настройка
  `FILES_THUMBNAIL_MAX_EDGE` (256). Зависимость `pillow` (runtime).
- Проверки: **305 тестов зелёные, 93.56%**, ruff/mypy strict/import-linter чисто;
  новый код покрыт (Pillow-адаптер 100%, `create_thumbnail`, сервис фичи 100%).
- **Статус:** закоммичено, помечено тегом `v0.7.1` и запушено в origin
  (`main` = `415072a`, тег `v0.7.1` на удалёнке). Origin синхронен.

## Обновление 2026-07-08 — бэклог v1.1: core/files + product_images (принято, тег `v0.7.0`, запушено)

Первый элемент бэклога после V1 (по команде владельца). Построены и **закоммичены**:

- **`core/files`** — модуль ядра (как billing, всегда включён): таблица `files`
  (tenant-RLS, ветка `core_files`), порт `StoragePort` + адаптеры `filesystem`
  (dev/test) и `s3` (boto3 + resilience; `build_storage` fail-loud при `s3` без
  кредов). `FileService.upload/get/open/delete` — валидация по **magic bytes**
  (клиентский Content-Type не доверяется; allowlist растровых картинок → inline
  XSS-safe), sha256. Права `files.file:*`, роутер `/api/files`.
- **`commerce.product_images`** (requires products + core files) — привязка картинок
  к товару (staff RBAC). `commerce_product_images` (product_id/file_id без
  межкомпонентных FK — валидация через сервисы), `/api/commerce/product-images`.
- **Зависимости:** boto3 + python-multipart (runtime), moto (dev). **Фикс изоляции
  тестов:** `_clean_db` чистит и Redis (per-IP лимит логина протекал между тестами).
- **Не построено (сознательно, доп. объём — по отдельной команде):** пресайн-URL;
  орфаны объектов при краше между put и commit задокументированы (GC — бэклог).
  Превью/тумбнейлы — построены отдельно (см. секцию выше, ждёт приёмки).
- Проверки: **295 тестов зелёные, 93.44%**, ruff/mypy strict/import-linter чисто,
  обратимость веток `core_files`+`commerce_product_images` в `test_migrations.py`.
  **Принято, помечено тегом `v0.7.0`, запушено в origin.**

## Итог: V1 готов, закалён боем и опубликован

Все 6 фаз V1 завершены и помечены тегами; конструктор проверен сборкой пилота
из шаблона; всё **запушено в origin** (`github.com/Shokhanasser1/backend-core`,
ветка `main` = `1f9babb`, теги `v0.1.0`…`v0.6.1`, origin синхронен).

| Тег | Фаза | Коммит |
|-----|------|--------|
| v0.1.0 | 1 — скелет/инфраструктура | 0daf123 |
| v0.2.0 | 2 — auth + tenants | 9168448 |
| v0.3.0 | 3 — billing + notifications + i18n | ef0a738 |
| v0.4.0 | 4 — audit + admin-каркас | 7d00003 |
| v0.5.0 | 5 — CLAUDE.md + документация | 6575e8e |
| v0.6.0 | 6 — commerce + конструктор | 0130517 |
| v0.6.1 | проверка боем: фикс add-feature | 418ed6e |

Плюс коммит `1f9babb` — харденинг-тест доставки события фичи через воркер (без тега).

Состояние проверок на конец сессии: **279 тестов зелёные, покрытие 93.23%**,
ruff/mypy strict/import-linter чисто, миграции всех веток обратимы. Рабочее дерево
чистое, `main` синхронен с `origin/main`.

## Что сделано в этой сессии (Фазы 4–6)

### Фаза 4 — Audit + Admin-каркас (v0.4.0)
`AuditService.search` + admin-экран аудита; ретенция (`audit_log` через
`app_retention`; `processed_events`/`notification_outbox` через `app_maintenance`);
admin-каркас (`AdminScreen`/`AdminRegistry`, `/api/admin/{slug}`,
`GET /api/admin/screens`, строгая валидация §5.4). **Два латентных фикса:**
(1) миграция `core_audit0002` — RLS-политики `app_retention` на `audit_log` (без
них свип удалял бы 0 строк); (2) валидатор прав переписан на
`iter_route_contexts` — в текущей версии FastAPI `include_router` создаёт ленивый
`_IncludedRouter`, и старый обход `app.routes` пропускал ВСЕ включённые роутеры
(инвариант «нет права → не стартует» по факту не применялся).

### Фаза 5 — Документация (v0.5.0)
`CLAUDE.md` расширен (конвенции именования, how-to по эндпоинту/миграции/модулю/
фиче, антипаттерны); README каждого модуля ядра; `docs/DEPLOYMENT.md` (прод-чеклист).

### Фаза 6 — Commerce + конструктор (v0.6.0)
Загрузчик фич (`modules/loader.py` + `app/features.py`), модуль commerce из трёх
фич (products/cart/orders) через публичные интерфейсы ядра, `tools/add-feature`,
тест честности манифестов, приёмочные тесты переноса, `examples/custom-delivery`,
README модуля. Детали — `CHANGELOG.md` и журнал `PLAN.md`.

### Проверка боем (после V1, v0.6.1 + `1f9babb`)
Собран пилот «магазин с корзиной» (products+cart+orders) из ядра через
`tools/add-feature` (клиент стартовал core-only, фичи втянуты цепочкой), поднят
против реального Postgres/Redis: миграции `upgrade heads` + сквозной сценарий
**товар→корзина→заказ→оплата→чек** прошёл; 15 фича-тестов пройдены в клиентском
проекте (тесты переезжают с фичей). **Найдено и исправлено 1 трение:** add-feature
падал на общей зависимости при инкрементальной сборке (`add cart` после
`add orders`) → теперь пропускает уже установленную зависимость (`v0.6.1`).
**Харденинг:** добавлен тест `tests/test_commerce_worker_dispatch.py` — реальная
reliable-доставка подписчика фичи через воркер (`dispatch_event` резолвит и
запускает `mark_order_paid`), закрыл пробел, который раньше только симулировался.
Серьёзных неудобств больше не выявлено — шаблон готов к реальному клиенту.

## Ключевые решения сессии (чтобы не переоткрывать)

- **ОВ-39 = (б)** (утверждено владельцем): доступ покупателя — `authenticated_endpoint`
  + ownership в сервисе. Магазин из заголовка `X-Shop-Tenant`; механизм —
  `storefront_bundle` в `core/auth/deps.py`. Реестр: `docs/phase0/00-open-questions.md`.
- **admin_registry сбрасывается per-app** (`create_app`) + core-экраны регистрируются
  явно (`register_admin_screens`), фича-экраны — загрузчиком; так меню и монтирование
  отражают ровно включённые модули. `install_modules` идёт ДО `mount_admin_screens`.
- **Воркер тоже ставит фичи** (`install_module_workers` в `app/worker.py`): reliable-
  подписчики фич и их шаблоны уведомлений должны быть зарегистрированы и в arq-воркере.
- **coverage `concurrency=["thread","greenlet"]`** обязателен: `TestClient` крутит
  приложение в отдельном потоке — без этого HTTP-исполняемый код (напр. сервис фичи)
  считается непокрытым (был баг измерения, покрытие «подскочило» 88.9%→93%).
- **`conftest.py` в корне** (не в `tests/`): фича-тесты берут фикстуры
  `commerce_client`/`commerce_payments_client` и НЕ импортируют `app` — иначе
  нарушение границы слоёв `modules → app` (import-linter).
- **Слои import-linter**: `app → modules → core → shared`. Фича-тесты живут в папке
  фичи (`modules/<m>/<f>/tests/`), `modules/` добавлен в testpaths/mypy/coverage.

## Что дальше → модуль saas (следующая работа)

Владелец решил взять **модуль `saas`** (бэклог v2 в `PLAN.md`): feature flags,
лимиты тарифов, usage metering, onboarding. Это ВТОРОЙ бизнес-модуль после
commerce — строится тем же паттерном (фичи + `feature.toml` + `README`-меню),
НЕ трогая заранее crm/tg-bot/copier.

**С чего начать новой сессии (перед кодом — прочитать):**
1. `master-prompt-backend-core.md` — архитектура/стандарты/анатомия фич/фазы
   (источник правды); `PLAN.md` — статус фаз и бэклог; `CLAUDE.md` — конвенции
   («Как добавить фичу», «Как добавить модуль ядра», антипаттерны).
2. **Эталон-образец** — модуль `commerce` (`modules/commerce/`, три фичи
   products/cart/orders) и его `README`: как бизнес-модуль собирается из фич через
   публичные интерфейсы ядра, загрузчик `modules/loader.py` + `app/features.py`,
   включение через `ENABLED_MODULES`.
3. `core/billing/README.md` — saas плотно опирается на billing (планы/подписки,
   `PaymentService`/`BillingService`, события `billing.*`); читать чужие таблицы
   нельзя — только публичные сервисы/`Directory`/события шины.

**Открытые вопросы к владельцу (решить до кода, в стиле Фазы 0 — не допускать
молча):**
- **saas = бизнес-модуль (`modules/saas/`) из фич или требует опоры в ядре?**
  Feature-flags/usage-metering, возможно, нужны и другим модулям (billing —
  для enфорса лимитов тарифа) → тогда часть переезжает в ядро/публичный
  `Directory`. По умолчанию — бизнес-модуль из фич, как commerce; уточнить.
- **Как лимиты тарифов связаны с планами billing?** (план → набор лимитов;
  где живёт справочник лимитов; кто и когда проверяет — сервис фичи или
  подписчик на `billing.subscription.activated`).
- **Usage metering:** что считаем (события шины? счётчики в Redis? периодические
  агрегаты в БД?), гранулярность, ретенция; тенантность обязательна.
- **Onboarding:** это данные (чек-лист прогресса) или оркестрация событий?
- Состав фич модуля и их `requires` (чужие таблицы не читать; зависимости вниз).

**Правила процесса (напоминание):** строго по одной фазе за раз; в конце фазы —
самопроверка из мастер-промпта, краткий отчёт, СТОП и ждать подтверждения; статус
и журнал обновлять в `PLAN.md`; общение по-русски, код/идентификаторы/комментарии
по-английски.

**Не в приоритете сейчас (по отдельной команде):** реальный клиентский проект;
модуль crm, tg-bot-template, copier-scaffolding; открытые ОВ-28/29/31 (юр-пакет и
хостинг — за владельцем/юристом, отражены в `docs/DEPLOYMENT.md`).

## Окружение (машина владельца, Windows)

- `uv` в `C:\Users\User\.local\bin` (в свежих шеллах не в PATH — добавлять вручную);
  `make` отсутствует (эквиваленты команд — в `README.md` «Команды без make» и Makefile).
- Docker Desktop есть, но не автозапущен — перед testcontainers/compose стартовать
  вручную и ждать `docker info`.
- **Git-Bash тут интерпретирует `>` и `->` в командах как редирект даже внутри
  кавычек** → плодит пустые файлы-мусор (`None`, `TenantContext`, `127` и т.п.).
  В bash-командах избегать `>`/`->`; перед коммитом проверять `git status` и чистить.

## Как проверить (CI-эквивалент)

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy app modules tools shared migrations tests
uv run lint-imports
uv run pytest --cov=app --cov=core --cov=shared --cov=modules --cov=tools \
  --cov-report=term-missing --cov-fail-under=85   # нужен запущенный Docker
```
