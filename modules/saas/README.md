# Модуль saas

Второй бизнес-модуль шаблона — набор инструментов SaaS-платформы поверх ядра
(auth, tenants, billing, notifications, audit, admin). Собирается тем же
паттерном, что и `commerce`: из независимых фич через публичные интерфейсы ядра.
Включается конфигом `ENABLED_MODULES=saas`; выключен — его роутов и RBAC в
приложении нет (таблицы существуют, если папки фич на диске: миграции привязаны к
наличию папки, а не к флагу).

## Фичи

| Фича | Назначение | Требует |
|------|-----------|---------|
| `saas.entitlements` | Права тарифа: feature flags + числовые лимиты, активный набор тенанта | core `auth`, `tenants`, `billing` |
| `saas.metering` | Учёт потребления: счётчики по метрикам, агрегаты по дням | core `auth`, `tenants` |

Планируется (строится по команде владельца — не заготавливается заранее):
`saas.onboarding` (чек-лист активации тенанта). Фичи — горизонтально независимы
(нет `requires_features` между ними), переносятся по отдельности.

## Рецепты сборки

- **Лимиты и фичефлаги тарифов:** `entitlements` (наполнить тарифную сетку
  seed-миграцией — см. `entitlements/README.md`).
- **Учёт потребления:** `metering` (вызывающий код метит через
  `MeteringService.record(...)` — см. `metering/README.md`).

Перенос в клиентский проект — `tools/add-feature`:

```bash
python -m tools.add_feature saas.entitlements /path/to/target
```

Затем в целевом проекте: `ENABLED_MODULES=saas` (через запятую с другими
модулями) и `python -m migrations.cli upgrade heads`.

## Связь с ядром

Модуль опирается на `billing` только через **события** (`billing.subscription.*`)
и публичные сервисы — чужие таблицы не читаются (ADR-0005). Лимиты живут в saas,
а не в billing: billing остаётся про деньги, saas — про права тарифа.

## Публичные интерфейсы фич (для соседей)

- `saas.entitlements` → `EntitlementService`: `is_enabled` / `get_limit` /
  `require_within_limit` / `snapshot`. Соседние бизнес-фичи (напр. `commerce`)
  enforce'ят лимиты тарифа, вызывая сервис, не читая таблицы saas.
- `saas.metering` → `MeteringService`: `record(metric, delta)` (учесть потребление)
  / `usage` / `summary`. Вызывающий код метит явно; metering не подписывается на
  шину (генеричный примитив, не зашивает чужие имена событий). Независим от
  `entitlements`.

## Как добавить новую фичу

По анатомии мастер-промпта (§АНАТОМИЯ ФИЧИ) и разделу «Как добавить фичу» в
`CLAUDE.md`: папка `modules/saas/<feature>/` с `feature.toml`, `models.py`,
`schemas.py`, `service.py`, `router.py`, опц. `subscribers.py`/`admin.py`,
`migrations/` (своя ветка Alembic), `tests/`, `README.md`. Зависимости только
вниз; к соседям — только через публичный интерфейс/события, объявленные в
манифесте. Честность манифеста проверяет `tests/test_manifest_honesty.py`.
