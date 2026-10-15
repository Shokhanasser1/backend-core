# crm.activities

Активности: звонки, встречи, задачи, письма — одна сущность и для плана дел, и для
истории касаний (как в Pipedrive). Третья фича модуля `crm`. Зависит от
`crm.contacts` и `crm.deals` (`requires_features = ["crm.contacts", "crm.deals"]`):
активность опционально связана с контактом, компанией и сделкой, которые
валидируются через публичные сервисы соседей — **чужие таблицы не читаются**.

## Назначение

Хранит активность тенанта: `type`, `subject`, `notes`, срок `due_at`, отметку
выполнения `done_at` (`NULL` — открыта), ответственного `assignee_user_id` (член
тенанта) и ссылки `contact_id` / `company_id` / `deal_id`.

**Типы — конфигурация клиента** (`CRM_ACTIVITY_TYPES`), в отличие от стадий сделок
с разумным дефолтом — фича работает сразу:

```dotenv
CRM_ACTIVITY_TYPES=call,meeting,task,email
```

Явно пустое значение = активности непригодны (create → `422`). Без явного типа —
первый из набора.

## Публичный интерфейс

`ActivityService` (реэкспорт в `activities/__init__.py`):

- `create(CreateActivityIn)` — ответственный по умолчанию = создатель; ответственный
  обязан быть активным членом тенанта (публичный `TenantDirectory.is_active_member`
  ядра), ссылки — существовать в тенанте (`ContactsService.ensure_contact/company`,
  `DealService.ensure_deal`); иначе `404`.
- `update(activity_id, ActivityChanges)` — PATCH: `None` = не менять; в HTTP явный
  `null` у `contact_id` / `company_id` / `deal_id` / `due_at` снимает значение. Смена
  ответственного публикует `crm.activity.assigned`.
- `complete(activity_id)` / `reopen(activity_id)` — идемпотентны (повтор — без события).
- `get(activity_id)` / `delete(activity_id)`.
- `list(page, ActivityQuery)` — фильтры: `assignee_user_id`, `mine` (мои), `status`
  (`open`/`done`), `overdue` (открытые с прошедшим сроком), `due_today` (срок в
  текущих сутках UTC), `type`, `deal_id`, `contact_id`, `company_id`. Порядок:
  открытые по сроку (без срока — в конце), затем новые.

Права проверяются и на роуте, и в сервисе (`PermissionGuard`). Напоминаний о сроках
нет (по решению владельца): клиент может повесить свои на события.

## Роуты

`GET|POST /api/crm/activities` (фильтры — query-параметры, + `limit`/`offset`),
`GET|PATCH|DELETE /api/crm/activities/{id}`, `POST /api/crm/activities/{id}/complete`,
`POST /api/crm/activities/{id}/reopen`.

## Права

`crm.activity:{read,create,update,delete}`. Owner/admin — всё; member — read/create/
update (в том числе complete/reopen), **не delete** (обязательный негатив: member →
`403` на DELETE).

## Таблица

`crm_activities` — тенантная (RLS, ветка Alembic `crm_activities`), FK `tenant_id →
tenants` (`ON DELETE RESTRICT`). `assignee_user_id` — без FK (пользователи
глобальны); `contact_id` / `company_id` / `deal_id` — без межфичевых FK (валидация
в сервисе).

## События

- **Публикует:** `crm.activity.{created,updated,deleted,completed,reopened}` и
  `crm.activity.assigned` (`{activity_id, from, to}`). Payload — идентификаторы.
- **Слушает** (reliable): `crm.contact.deleted`, `crm.company.deleted`,
  `crm.deal.deleted` — снимает ссылку со всех активностей тенанта (публикует
  `crm.activity.updated` по каждой); сама активность (история) остаётся.

## Перенос в клиентский проект

```bash
python -m tools.add_feature crm.activities /path/to/target
```

`tools/add-feature` тянет цепочку `crm.deals` → `crm.contacts`. Затем
`ENABLED_MODULES=crm`, при желании `CRM_ACTIVITY_TYPES` и
`python -m migrations.cli upgrade heads`.
