# saas.onboarding

Чек-лист активации тенанта: прогресс по настраиваемым шагам. Независимая фича
(`requires_features = []`), опирается на ядро `auth` + `tenants`.

## Назначение

Данные о прогрессе активации (что уже настроено: создан магазин, добавлен товар,
подключён платёж…) — для UI-визарда «первые шаги». **Набор шагов — конфигурация
клиентского проекта** (`SAAS_ONBOARDING_STEPS`), а не жёстко зашитый список: шаблон
не знает пути активации конкретного клиента.

## Публичный интерфейс

`OnboardingService` (реэкспорт в `onboarding/__init__.py`):

- `complete_step(step_key) -> OnboardingProgressDTO` — отметить шаг выполненным
  (идемпотентно; ключ вне конфигурации → `InvariantViolationError`/422). Пишется в
  транзакции вызывающего. Публикует `saas.onboarding.completed` **ровно один раз** —
  когда этим вызовом закрыт последний оставшийся шаг.
- `progress() -> OnboardingProgressDTO` — чек-лист: по одной записи на каждый
  сконфигурированный шаг (в порядке конфигурации) + `completed_count`/`total`/
  `is_complete`.

Шаги отмечаются **явно** (решение владельца): серверный glue / соседние фичи зовут
`complete_step` на вехе (напр. при `commerce.order.paid`), а фронт — через POST для
шагов, которые отмечает пользователь. Onboarding **не подписывается на шину** (фичам
запрещён wildcard, generic-чек-лист не зашивает чужие имена событий).

## Роуты

- `GET /api/saas/onboarding/me` (`saas.onboarding:read`) — прогресс тенанта.
- `POST /api/saas/onboarding/steps/{step_key}/complete` (`saas.onboarding:update`) —
  отметить шаг (идемпотентно).

## Права

`saas.onboarding:read` + `saas.onboarding:update` (owner/admin — активация это
задача настройки организации).

## Таблица

`saas_onboarding_progress` — тенантная (RLS, ветка Alembic `saas_onboarding`): одна
строка на выполненный шаг (`(tenant_id, step_key)` уникальны), `completed_at`.
Отсутствие строки = шаг не выполнен.

## События

Публикует `saas.onboarding.completed` (payload `{}`) — когда закрыт последний
сконфигурированный шаг. Ничего не слушает.

## Конфигурация шагов

```dotenv
SAAS_ONBOARDING_STEPS=create_shop,add_product,connect_payment
```

Пустое значение = чек-листа нет (`total = 0`, `is_complete = false`).

## Перенос в клиентский проект

```bash
python -m tools.add_feature saas.onboarding /path/to/target
```

Затем `ENABLED_MODULES=saas`, задать `SAAS_ONBOARDING_STEPS` и
`python -m migrations.cli upgrade heads`.
