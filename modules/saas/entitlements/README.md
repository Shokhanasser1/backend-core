# saas.entitlements

Права тарифа: **feature flags** (булевы) и **числовые лимиты** плана + активный
набор тенанта. Фундамент модуля `saas`. Независимая фича (`requires_features = []`),
опирается на ядро `auth` + `tenants` + события `billing`.

## Назначение

Билинг отвечает за деньги и подписки и ничего не знает о лимитах. Эта фича
переводит активный тариф тенанта в набор прав: что включено (флаги) и сколько
можно (лимиты). Соседние фичи спрашивают у `EntitlementService`, не читая её
таблицы.

## Публичный интерфейс

`EntitlementService` (реэкспорт в `entitlements/__init__.py`):

- `is_enabled(flag_key) -> bool` — включён ли флаг активного плана (нет → `False`).
- `get_limit(limit_key) -> int | None` — числовой лимит (`None` = безлимит/не задан).
- `require_within_limit(limit_key, current_count)` — гард для соседей: бросает
  `ConflictError` (409), если создание ещё одного объекта превысит лимит.
  `current_count` — число ДО нового объекта. No-op, если лимит не задан.
- `snapshot() -> EntitlementsDTO` — все флаги/лимиты активного плана (для `GET /me`).
- `effective_plan_code() -> str | None` — активный `plan_code` (учёт отмены).

Пример enforcement в соседней фиче (без чтения таблиц saas):

```python
# modules/commerce/products/service.py (гипотетически)
await entitlements.require_within_limit("commerce.product", await self._repo.count())
```

## Права

`saas.entitlement:read` — обзор entitlements тенанта (owner/admin). Enforcement
лимитов идёт через сервис в вызывающей фиче, не через это право.

## События

- **Публикует:** `saas.entitlement.changed` (`{plan_code}`) — при смене активного
  плана / отмене.
- **Слушает** (reliable): `billing.subscription.activated`
  (`{subscription_id, plan_code, current_period_end}`) → ставит активный план;
  `billing.subscription.canceled` (`{subscription_id, plan_code}`) → помечает
  отмену (покрытие держится до конца периода).

## Таблицы

- `saas_plan_entitlements` — **глобальный справочник** (тарифная сетка): строка на
  `(plan_code, entitlement_key)`, `kind ∈ {flag, limit}`, `bool_value`/`int_value`.
  Read-only в рантайме; клиент проекта наполняет её seed-миграцией. `plan_code` —
  «голый» код плана billing (без межкомпонентного FK).
- `saas_tenant_entitlements` — **тенантная** (RLS): активный `plan_code`,
  `current_period_end`, `canceled`. Одна строка на тенант; ведётся подписчиками.

## Дефолты (осознанно — это шаблон)

- Нет активного плана (нет подписки / отменённая после конца периода) →
  «несконфигурированное» состояние: флаги `False`, лимиты `None` (безлимит).
- Флаг вне сетки → `False`; лимит вне сетки → `None`. Enforcement срабатывает
  только там, где лимит **явно** задан, — включение фичи не блокирует тенанта
  целиком. Нужен жёсткий пол — выдайте всем free-план billing (авто-подписка) с
  явными лимитами.

## Как наполнить тарифную сетку

Сетка — конфигурация продукта (как `plans` в billing). Заполняется seed-миграцией
клиентского проекта, например:

```sql
INSERT INTO saas_plan_entitlements (plan_code, entitlement_key, kind, bool_value, int_value) VALUES
  ('free', 'commerce.product',  'limit', NULL, 10),
  ('free', 'commerce.api',      'flag',  false, NULL),
  ('pro',  'commerce.product',  'limit', NULL, 1000),
  ('pro',  'commerce.api',      'flag',  true,  NULL);
```

## Перенос в клиентский проект

```bash
python -m tools.add_feature saas.entitlements /path/to/target
```

Затем `ENABLED_MODULES=saas` и `python -m migrations.cli upgrade heads`.
