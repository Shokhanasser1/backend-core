# crm.activities — дизайн (утверждён владельцем 2026-10-15)

Третья фича модуля `crm` после `crm.contacts` и `crm.deals`. В плане значилась как
`crm.tasks` («задачи/активности»); по решению владельца это единая сущность
**активность** (как в Pipedrive) — фича называется `crm.activities`.

## Решения владельца

| Вопрос | Решение |
|---|---|
| Что хранит фича | Единая «активность»: звонок, встреча, задача, email… — и план дел, и история касаний одной таблицей |
| Типы активностей | Конфигом `CRM_ACTIVITY_TYPES` с дефолтом `call,meeting,task,email` |
| Связи | Контакт, компания и сделка (все опциональны) → `requires crm.deals` |
| Ответственный | Да: активный член тенанта; по умолчанию — создатель |
| Напоминания о сроках | Нет в этой итерации: срок + фильтры + события (клиент может повесить свои напоминания на события) |
| Имя | `crm.activities` (таблица `crm_activities`, права `crm.activity:*`, события `crm.activity.*`) |

## Данные

Таблица `crm_activities` — тенантная (RLS через `enable_tenant_rls`, ветка Alembic
`crm_activities`, `depends_on = shared0002`), FK `tenant_id → tenants`
(`ON DELETE RESTRICT`).

| Колонка | Тип | Смысл |
|---|---|---|
| `type` | text, NOT NULL | один из `CRM_ACTIVITY_TYPES` |
| `subject` | text, NOT NULL | краткое описание (1..200) |
| `notes` | text | заметка (≤ 2000) |
| `due_at` | timestamptz | срок (опционально) |
| `done_at` | timestamptz | `NULL` = открыта; время выполнения |
| `assignee_user_id` | uuid, NOT NULL | ответственный (член тенанта), без FK — users глобальны, владелец core/auth |
| `contact_id`, `company_id`, `deal_id` | uuid | связи с фичами-соседями — «голые» Uuid, без межфичевого FK |

Индексы: `(tenant_id, assignee_user_id, done_at)`, `(tenant_id, due_at)`,
`(tenant_id, deal_id)`, `(tenant_id, contact_id)`.

**Конфиг.** `CRM_ACTIVITY_TYPES` (csv). В отличие от `CRM_DEAL_STAGES` у типов есть
дефолт `call,meeting,task,email`: фича работает сразу. Явно пустое значение = фича
непригодна (create → 422), как сделки без стадий.

## Зависимости и границы

- `feature.toml`: `requires_features = ["crm.deals"]` (транзитивно `crm.contacts`),
  `requires_core = ["auth", "tenants"]`.
- Ссылки проверяются через публичные интерфейсы соседей — только существование в
  тенанте, без прав соседа: `ContactsService.ensure_contact/ensure_company` (есть),
  `DealService.ensure_deal` (добавить). Чужой/несуществующий id → 404.
- Ответственный — через публичный `TenantDirectory` ядра: новый метод
  `is_active_member(tenant_id, user_id) -> bool` (активное членство в активном
  тенанте). Не член → 404. Таблицы `memberships`/`users` фича не читает.

## Сервис и API

`ActivityService` (публичный интерфейс, реэкспорт в пакете):

- `create(subject, type=None, notes=None, due_at=None, assignee_user_id=None, contact_id=None, company_id=None, deal_id=None)` —
  `type=None` → первый тип из конфигурации; ответственный по умолчанию = актор.
- `update(activity_id, ActivityChanges)` — объект изменений (как `DealChanges`):
  `None` = не менять; явный JSON `null` у `contact_id`/`company_id`/`deal_id`/`due_at`
  снимает значение. Смена ответственного → событие `crm.activity.assigned`.
- `complete(activity_id)` / `reopen(activity_id)` — идемпотентны (повтор — без события).
- `get(activity_id)`, `delete(activity_id)`.
- `list(page, *, assignee_user_id=None, mine=False, status=None, overdue=False, due_today=False, type=None, deal_id=None, contact_id=None, company_id=None)` —
  `status ∈ {open, done}`; «просроченные» = открытые с `due_at < now`; «на сегодня» —
  `due_at` в текущих сутках UTC; сортировка: открытые по сроку, затем новые.
- Сервисные методы проверяют права (`PermissionGuard`); снятие ссылок подписчиками
  (`unlink_*`) — внутренние, без guard.

Роуты `/api/crm/activities`: `GET` (список с фильтрами-query), `POST`, `GET /{id}`,
`PATCH /{id}`, `DELETE /{id}`, `POST /{id}/complete`, `POST /{id}/reopen`.

## Права

`crm.activity:{read,create,update,delete}`. Owner/admin — всё; member — read/create/
update (включая complete/reopen), без delete — как у сделок. Проверка на роуте и в
сервисе.

## События

- **Публикует:** `crm.activity.created`, `crm.activity.updated`,
  `crm.activity.deleted`, `crm.activity.completed`, `crm.activity.reopened`,
  `crm.activity.assigned` (`{activity_id, from, to}`). Payload — идентификаторы.
- **Слушает** (reliable): `crm.contact.deleted`, `crm.company.deleted`,
  `crm.deal.deleted` → снимает соответствующую ссылку со всех активностей тенанта
  (история не теряется), по каждой — `crm.activity.updated`.

## Ошибки

- Неизвестный тип / пустая конфигурация типов → 422.
- Чужие/несуществующие активность, контакт, компания, сделка, ответственный → 404.
- `subject` пустой/длинный, `notes` > 2000 → 422 (схема).
- Нет права → 403 (роут и сервис); без токена → 401.

## Тесты

- Сквозной happy-path: создание со связями и ответственным, список/фильтры,
  complete/reopen (идемпотентность, события), PATCH со снятием ссылки.
- Негативы: member → 403 на DELETE; 401 без токена; чужой/несуществующий
  контакт/компания/сделка/ответственный → 404; неизвестный тип → 422.
- Изоляция тенантов (RLS).
- Фильтры «просроченные», «мои», «на сегодня».
- Подписчики снимают ссылки при удалении контакта/компании/сделки.
- Сервисный guard: member без delete получает отказ в самом сервисе.
- Честность манифеста — общий `tests/test_manifest_honesty.py`.

## Документация

README фичи; README модуля crm (таблица фич, рецепт «работа с клиентом»);
PLAN.md («tasks» → «activities»); CHANGELOG `0.15.0`; `.env.example`
(`CRM_ACTIVITY_TYPES`); `TemplateDef`/шаблоны не нужны (уведомлений нет).

## Вне объёма

Напоминания о сроках, повторяющиеся активности, вложения, календарь/iCal — по
отдельной команде.
