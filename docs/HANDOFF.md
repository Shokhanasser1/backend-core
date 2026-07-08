# HANDOFF — состояние проекта после сессии 2026-07-08

Хендофф для следующей сессии/владельца. Источники правды остаются:
`master-prompt-backend-core.md` (требования), `PLAN.md` (фазы + журнал),
`CLAUDE.md` (конвенции разработки), `docs/phase0/00-open-questions.md` (реестр
решений ОВ).

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

## Что дальше

Хорошо определённая работа V1 + проверка боем исчерпаны; следующее направление —
за владельцем (по правилам проекта бэклог не берётся без отдельной команды):

1. **Реальный/пилотный клиентский проект** из шаблона (проверка боем на боевом
   бизнесе, а не на синтетическом пилоте) — правки неудобств возвращать в шаблон.
2. **Бэклог** (только по отдельной команде, `PLAN.md`): core/files + product_images
   ✅ сделано (v1.1, ждёт тега/push); остаётся — превью/тумбнейлы к product_images,
   Stripe-адаптер, модули saas/crm, tg-bot-template, copier-scaffolding.
3. **Открытые нерешённые ОВ** (не блокируют код): ОВ-28/29/31 — юр-пакет и хостинг
   (решения владельца/юриста, отражены в `docs/DEPLOYMENT.md`).

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
