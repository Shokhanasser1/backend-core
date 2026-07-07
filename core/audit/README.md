# core/audit

## Назначение

Append-only журнал значимых действий: кто, что, когда, откуда (`request_id`/
`tenant_id`/`user_id`/`ip`). Гибридный — есть системные (tenant_id NULL) и
анонимные записи; без FK (журнал переживает удаление любых сущностей). Неизменяем:
методов update/delete нет, а на уровне БД UPDATE не выдан никому, DELETE — только
роли `app_retention`.

## Публичный интерфейс

- **`AuditService`** (`service.py`):
  - `record(...)` — прямая запись в транзакции бизнес-действия (для критичных
    действий, с `event_id` события той же транзакции — дедуп «одно действие = одна
    запись»);
  - `record_event(envelope)` — путь wildcard-стока (идемпотентно по `event_id`);
  - `search(query, page) -> PageResult[AuditRecordDTO]` — чтение для admin-экрана
    (фильтры action-префикс/актор/объект/даты, тенант-скоуп).
- **Admin-экран `audit`** (`admin.py`): `GET /api/admin/audit`, право
  `audit.record:read` (owner/admin).
- **Ретенция** (`retention.py`): `purge_expired_audit` — свип как `app_retention`
  (OV-27, дефолт 24 мес), джоба воркера `purge_retention`.

## Как audit получает события

`subscribers.py` — **wildcard-подписчик** `bus.subscribe("*", reliable=True,
maintenance=True)`: подключение любого модуля НЕ требует правок audit (ядро не
знает чужих имён событий). Высокочастотная телеметрия из exclusion-списка
(`notifications.message.sent`) в журнал не дублируется.

## Права (владеет)

`audit.record:read` (owner/admin).

## Как расширять

- **Критичное действие писать напрямую:** в сервисе `event_id = self.emit(...)` и
  `audit.record(..., event_id=event_id)` в одной транзакции — wildcard-сток потом
  отбросит дубль по `event_id`. Остальное попадёт в журнал автоматически стоком.
- Не логировать в `payload` секреты/полные ПД — только идентификаторы и изменённые
  поля.

## Не публично

Таблица `audit_log`, wildcard-подписчик, exclusion-список. Append-only —
гарантия формы интерфейса И грантов БД.
