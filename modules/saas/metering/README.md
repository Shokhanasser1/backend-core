# saas.metering

Учёт потребления (usage): счётчики по метрикам, агрегированные по дням. Независимая
фича (`requires_features = []`), опирается на ядро `auth` + `tenants`.

## Назначение

Генеричный примитив для измерения потребления тенантом (сколько заказов, вызовов
API, отправленных SMS и т.п.). **Metering не подписывается на шину** — фичам
запрещены wildcard-подписки, а универсальный счётчик не должен зашивать чужие
имена событий. Вместо этого вызывающий код метит **явно**:
`MeteringService.record(metric, delta)` в нужной доменной точке.

## Публичный интерфейс

`MeteringService` (реэкспорт в `metering/__init__.py`):

- `record(metric_key, delta=1, *, at=None)` — атомарный UPSERT счётчика тенанта за
  UTC-день `at` (по умолчанию сейчас). Пишется в текущей транзакции вызывающего —
  атомарно с измеряемым фактом. **Не идемпотентен сам по себе:** кто не должен
  двоить, метит внутри reliable-обработчика события (dedup `processed_events`
  делает весь обработчик effectively-once → и `record` тоже).
- `usage(metric_key, *, since=None, until=None) -> int` — сумма по метрике за окно
  дней `[since, until]` (включительно; границы опциональны).
- `summary(*, since=None, until=None) -> dict[str, int]` — суммы по всем метрикам
  тенанта за окно (для `GET /me`).

Пример инструментации (в клиентском glue-подписчике или бизнес-фиче):

```python
# при оплате заказа
await metering.record("commerce.order_paid")
```

## Права

`saas.usage:read` — обзор потребления тенанта (owner/admin). Запись (`record`) —
серверный вызов сервиса, роута/права не требует.

## Таблица

`saas_usage_counters` — тенантная (RLS), одна строка на `(tenant, metric, день)`:
`bucket` (Date), `value` (BigInteger). Уникальный индекс
`(tenant_id, metric_key, bucket)` — он же цель UPSERT и опора периодических
запросов. Дневная гранулярность держит таблицу маленькой (нет сырого лога событий)
и даёт ретенции естественный порог.

## Ретенция

Bucket'ы старше `SAAS_USAGE_RETENTION_DAYS` (по умолчанию 400 ≈ 13 мес) чистит
суточная джоба воркера `purge_retention` (как `app_maintenance`, кросс-тенантно),
**только при включённом** `ENABLED_MODULES=saas` (ленивый импорт в `app/worker.py`).
Свип батчами (как `audit_log`/`processed_events`).

## Независимость от entitlements

Metering и `saas.entitlements` **не связаны** (решение владельца): metering —
учёт/отчёты, лимиты-счётчики (max N объектов) остаются в entitlements через
`current_count` вызывающей фичи. Если позже понадобятся «лимиты на потребление за
период» — `require_within_limit` для таких ключей мог бы читать `usage()` (тогда
`entitlements requires metering`).

## Перенос в клиентский проект

```bash
python -m tools.add_feature saas.metering /path/to/target
```

Затем `ENABLED_MODULES=saas` и `python -m migrations.cli upgrade heads`.
