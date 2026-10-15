# Модуль crm

Третий бизнес-модуль шаблона (после `commerce` и `saas`) — лёгкий CRM поверх ядра
(`auth`, `tenants`). Собирается загрузчиком из независимых фич; включается
`ENABLED_MODULES=crm`. **Независим от `commerce`** — клиент включает те модули,
что ему нужны; связку с заказами (если понадобится) добавим позже через события /
публичные сервисы, без чтения чужих таблиц.

## Фичи

| Фича | Назначение | requires |
|------|-----------|----------|
| `crm.contacts` | Адресная книга: люди (contacts) + организации (companies) | core `auth`, `tenants` |
| `crm.deals` | Сделки + воронка по настраиваемым стадиям | feature `crm.contacts` |
| `crm.activities` | Активности: звонки, встречи, задачи, письма — срок, ответственный, связи | features `crm.contacts`, `crm.deals` |

Согласованный состав собран: contacts → deals → activities (в плане значилась как
`crm.tasks`; владелец выбрал единую сущность «активность»).

## Рецепты сборки

- **Адресная книга:** `contacts` (люди/компании через `ContactsService`; связь
  contact→company с `ON DELETE SET NULL` — см. `contacts/README.md`).
- **Воронка продаж:** `deals` (стадии — конфигом `CRM_DEAL_STAGES`; суммы Money;
  связь с контактом/компанией через `ContactsService` — см. `deals/README.md`).
- **Работа с клиентом:** `activities` (звонки/встречи/задачи с ответственным и
  сроком, привязка к контакту/компании/сделке; типы — `CRM_ACTIVITY_TYPES` — см.
  `activities/README.md`).

Перенос в клиентский проект — `tools/add-feature`:

```bash
python -m tools.add_feature crm.contacts /path/to/target
```

Затем `ENABLED_MODULES=crm` и `python -m migrations.cli upgrade heads`.

## Публичные интерфейсы

- `crm.contacts` → `ContactsService`: CRUD компаний и контактов; `list_contacts`
  фильтруется по `company_id`. Соседние фичи читают людей/компании через сервис,
  не через таблицы. Публикует `crm.company.*` / `crm.contact.*`.
- `crm.deals` → `DealService`: CRUD сделок; стадии из `CRM_DEAL_STAGES`, суммы
  Money, связи валидируются через `ContactsService`. Публикует `crm.deal.*`
  (включая `crm.deal.stage_changed`).
- `crm.activities` → `ActivityService`: активности с типом из `CRM_ACTIVITY_TYPES`,
  сроком, ответственным (член тенанта) и связями; фильтры «мои/просроченные/на
  сегодня». Публикует `crm.activity.*`; снимает ссылки при `crm.contact|company|deal.deleted`.

## Как добавить новую фичу

Повтори анатомию `crm.contacts` (см. CLAUDE.md «Как добавить фичу»): самодостаточная
папка с `feature.toml`, зависимости только вниз (фича → ядро → shared),
горизонталь — только через публичные сервисы/события, чужие таблицы не читать.
