# CLAUDE.md

## Что это за проект

backend-core — НЕ приложение, а переиспользуемый шаблон-конструктор
(starter kit) для сборки клиентских проектов под рынок Узбекистана/СНГ:
e-commerce, SaaS, CRM, боты. Архитектура — три уровня: **ядро → модули
→ фичи**. Commerce — первый модуль; crm, saas и другие добавляются тем
же паттерном.

Перед любой работой прочитай:
- `master-prompt-backend-core.md` — источник правды: архитектура,
  стандарты, анатомия фич, фазы;
- `PLAN.md` — текущий статус фаз и бэклог.

## Правила работы

- Строго по одной фазе за раз (фазы — в PLAN.md). В конце фазы:
  самопроверка из мастер-промпта, краткий отчёт, стоп — жди
  подтверждения пользователя.
- Ничего из бэклога не строить заранее (files, Stripe, saas, crm,
  tg-bot и т.д.) — даже пустых папок «на потом».
- Общение с пользователем — на русском. Код, идентификаторы,
  комментарии — на английском. Документация проекта (README, ADR) —
  на русском.
- Неоднозначное требование → вопрос пользователю, не молчаливое
  допущение.
- После завершения фазы обновляй статус в PLAN.md (журнал + таблица).

## Ключевые архитектурные решения (детали — в мастер-промпте)

- Модульный монолит. Python 3.12+, FastAPI, SQLAlchemy 2.x (async),
  Alembic, PostgreSQL 16, Redis, arq, Docker Compose. Пакетный
  менеджер — uv.
- Мультитенантность: `tenant_id` во всех бизнес-таблицах +
  автофильтрация в репозитории + RLS в Postgres.
- Модули включаются конфигом `ENABLED_MODULES`. Фича — самодостаточная
  папка с `feature.toml` (requires проверяются на старте приложения,
  тестом в CI и при переносе через tools/add-feature).
- Зависимости только «вниз»: фича → ядро → shared. Горизонтально —
  только публичные сервисные интерфейсы или события шины. Чужие
  таблицы не читать никогда.
- Каждый эндпоинт обязан декларировать права (`require_permission`),
  иначе приложение не стартует.

## Definition of done (каждая фаза)

`make lint` и `make test` зелёные; ни одного TODO/заглушки в
прод-путях; новые таблицы с tenant_id + тест изоляции тенантов;
эндпоинты с декларацией прав + негативный тест на 403; фичи с
feature.toml и README, манифесты честные (тест импортов); секретов
в коде нет.

## Статус

Ядро построено и принято: Фазы 0–4 (теги `v0.1.0`…`v0.4.0`). Готово —
auth, tenants, billing (Payme/Click), notifications (Telegram/Eskiz/SMTP),
audit, admin-каркас, i18n (ru/uz). Идёт Фаза 5 (документация). Дальше —
Фаза 6: модуль commerce + загрузчик фич + `tools/add-feature`. Актуальный
статус и журнал — `PLAN.md`.

Ниже — рабочие конвенции: как добавить эндпоинт, миграцию, модуль, фичу,
и чего не делать. У каждого модуля ядра есть свой `README.md` с публичным
интерфейсом и событиями (`core/<module>/README.md`). Чек-лист продакшена —
`docs/DEPLOYMENT.md`.

---

## Конвенции именования

- **Таблицы.** Таблицы фич бизнес-модулей несут префикс модуля:
  `commerce_carts`, `commerce_orders` (ОВ-08). Таблицы ядра — простые имена
  (`users`, `tenants`, `audit_log`, `notification_outbox`). Глобальные
  справочники — без тенанта (`currencies`, `plans`).
- **Права:** `<module>.<resource>:<action>` — модуль без имени фичи, ресурс
  в ед. числе snake_case, action из `read|create|update|delete` или
  зарегистрированный глагол (`cancel`, `invite`, `manage`). Примеры:
  `tenants.member:invite`, `billing.subscription:manage`, `audit.record:read`.
  Формат проверяет регэксп в `core/auth/permissions.py`; незарегистрированный
  код в `require_permission` — ошибка старта.
- **События:** `<module>.<entity>.<action>` — модуль без имени фичи, сущность
  в ед. числе, action глаголом в прошедшем времени (событие — свершившийся
  факт): `auth.user.registered`, `billing.payment.succeeded`,
  `tenants.member.removed`. Публикуются только после commit (ADR-0006).
- **Миграции:** файл `<YYYYMMDD>_<branch><NNNN>_<slug>.py`, напр.
  `20260707_core_audit0002_retention_policy.py`. Внутри — `revision`,
  `down_revision`, `branch_labels` (только у ПЕРВОЙ ревизии ветки), `depends_on`.
- **Ветки Alembic:** одна ветка на компонент: `shared`, `core_auth`,
  `core_tenants`, `core_billing`, `core_notifications`, `core_audit`. Новый
  модуль ядра = новая ветка. Ветки собираются автодискавери (`migrations/
  discovery.py` сканит `core/*/migrations`, `modules/*/*/migrations`), центральный
  файл править не нужно.
- **DB-роли:** `app_migrator` (владелец схемы, миграции), `app_user` (рантайм,
  под RLS), `app_maintenance` (кросс-тенантные джобы), `app_retention` (только
  DELETE `audit_log`). Детали — `shared/db_provisioning.py`, схема §3.1.

## Как добавить эндпоинт

1. В `router.py` объяви ровно ОДИН маркер прав (иначе приложение не стартует,
   `app/startup_checks.py` + CI-тест `tests/test_startup_checks.py`):
   - `require_permission("<code>")` — член тенанта с правом (RBAC). Код обязан
     быть зарегистрирован (см. ниже);
   - `authenticated_endpoint("reason")` — любой аутентифицированный, тенант не
     нужен (`/me`, создание своей организации); авторизация объекта — в сервисе;
   - `public_endpoint("reason")` — без JWT (login, register, вебхуки платёжек).
2. Проверь право **и на уровне сервиса** — роутер не единственная линия
   (`AccessService.require(...)` или проверка принадлежности объекта `ctx.actor`).
3. Включи роутер в `app/main.py` (`application.include_router(...)`).
4. Тесты: сквозной happy-path + **обязательный негатив на 403** (член без права)
   и на 401 (без токена). Пример — `tests/test_billing_api.py`.

## Как добавить миграцию

1. Файл в `<component>/migrations/` по конвенции имён выше. `down_revision` —
   предыдущая ревизия ЭТОЙ ветки; `branch_labels` ставится только у первой
   ревизии ветки; `depends_on = "shared0002"`, если нужны RLS-функции/роли.
2. **Новая бизнес-таблица:** колонка `tenant_id` (NOT NULL для тенантных),
   включить RLS и политики — используй `enable_tenant_rls(...)` из `shared/rls.py`
   (политики `tenant_isolation` для `app_user`, `maintenance_all` для
   `app_maintenance`) + `GRANT ... TO app_user, app_maintenance`. Образец —
   `core/tenants/migrations/...tenant_tables.py`.
3. **Рабочий `downgrade`** обязателен (тест `tests/test_migrations.py` гоняет
   upgrade heads + downgrade до base каждой ветки).
4. Применить: `python -m migrations.cli upgrade heads`.

## Как добавить модуль ядра

Повтори анатомию существующего модуля (напр. `core/billing`):

1. `core/<module>/`: `models.py`, `schemas.py`, `service.py`, `router.py`,
   `permissions.py`, `migrations/` (новая ветка `core_<module>`).
2. **Права:** объяви коды в `permissions.py` и функцию `register_<module>_rbac()`
   — она зовёт `register_permissions("<module>", [...])` и (если модуль гейтит
   роуты) `system_role_grants.extend({...})`, чтобы выдать коды системным ролям
   owner/admin/member (см. `core/billing/permissions.py`).
3. **Подписчики шины:** если реагируешь на события — добавь импорт своего
   `subscribers.py` в `core/subscribers.py` (он импортируется и web-, и
   worker-процессом).
4. **Admin-экран** (по желанию): создай `admin.py` с `AdminScreen` +
   `admin_registry.register(...)`, добавь импорт в `core/admin/screens.py`
   (см. `core/audit/admin.py`).
5. **Чтение соседями:** если другим модулям нужны твои данные — дай публичный
   `Directory` (read-only, без утечки ORM, см. `core/tenants/directory.py`).
   Соседи НИКОГДА не читают твои таблицы напрямую.
6. **Композиционный корень** `app/main.py`: вызови `register_<module>_rbac()`
   до валидатора прав и `application.include_router(...)`.
7. `README.md` модуля (назначение, публичный интерфейс, события, как расширять).

## Как добавить фичу (бизнес-модуль)

Фича — самодостаточная папка (анатомия — мастер-промпт §АНАТОМИЯ ФИЧИ):
`feature.toml` (манифест связей: `requires_features`, `requires_core`,
`owns_tables`, `publishes_events`, `listens_events`), `models.py`, `schemas.py`,
`service.py`, `router.py`, `admin.py`, `migrations/`, `tests/`, `README.md`.
Зависимости только вниз (фича → ядро → shared); горизонталь — только через
публичные сервисы/события; чужие таблицы не читать.

**Загрузчик фич (автодискавери `feature.toml`, проверка `requires` на старте,
тест честности манифестов) и `tools/add-feature` — Фаза 6, ещё не построены.**
До неё модуль `commerce` не собирается. Не строить заранее.

## Точечные рецепты

- **Право** → добавь `PermissionDef` в `permissions.py` модуля; выдай ролям через
  `system_role_grants.extend(...)`. Синк в `role_permissions` — на старте
  (`core/tenants/sync.py`).
- **Событие** → издатель: `self.emit("<module>.<entity>.<action>", payload)` в
  сервисе (уходит после commit). Подписчик: `@bus.subscribe("name", reliable=...)`,
  импорт в `core/subscribers.py`. Wildcard/`*` — привилегия ядра (только audit).
- **Шаблон уведомления** → `register_templates("<module>", [...], dir)` +
  файлы `templates/<locale>/<key>.txt` для КАЖДОЙ локали (ru, uz обязательны —
  парити проверяется на старте). См. `core/notifications/registry.py`.
- **Платёжный провайдер** → реализуй порт `PaymentProvider` (`core/billing/ports.py`)
  в `core/billing/adapters/<name>.py`, включи в `ENABLED_PAYMENT_PROVIDERS`.
- **Канал уведомлений** → реализуй порт `NotificationChannel`
  (`core/notifications/ports.py`); dormant-by-default (нет кредов → no-op).
- **Admin-экран** → см. «Как добавить модуль ядра», п. 4.

## Антипаттерны (никогда не делай — потому что)

- **Читать таблицы чужого модуля/фичи** — ломает переносимость и границы
  (import-linter завернёт). Нужен доступ — публичный сервис/`Directory`/событие.
- **Роут без маркера прав или с двумя** — приложение не стартует; не «временно».
- **Проверять права только в роутере** — дублируй в сервисе (роутер обходят
  джобы, шина, тесты).
- **Импорт `app` из `core`/`shared`** — только «вниз» (`app → core → shared`);
  рантайм-объекты бери из `request.app.state`, не импортом.
- **`float` для денег** — только целые минимальные единицы + `currency`; экспонента
  из справочника (`shared/money.py`).
- **`except Exception: pass`** — не глотать исключения; внешние сбои — типизированные
  `ExternalServiceError`/circuit breaker, не тишина.
- **Публиковать событие до commit** — только post-commit (иначе подписчик увидит
  откатанные данные). Идёт через `self.emit(...)`.
- **Секрет/полные ПД/токен/код 2FA в логах, событиях, `audit.payload`** — только
  идентификаторы и изменённые поля. Каналы маскируют адреса.
- **Новая бизнес-таблица без `tenant_id`, RLS и теста изоляции** — дыра
  мультитенантности; RLS — обязательная вторая линия после автофильтра Repository.
- **Строить бэклог заранее** (files/S3, Stripe, saas, crm, tg-bot) — даже пустых
  папок «на потом» (мастер-промпт §ЧЕГО НЕ ДЕЛАТЬ).
