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

Планируется (строится по команде владельца — не заготавливается заранее):
`crm.tasks` (задачи/активности, requires contacts). Состав согласован, порядок:
contacts → deals → tasks.

## Рецепты сборки

- **Адресная книга:** `contacts` (люди/компании через `ContactsService`; связь
  contact→company с `ON DELETE SET NULL` — см. `contacts/README.md`).
- **Воронка продаж:** `deals` (стадии — конфигом `CRM_DEAL_STAGES`; суммы Money;
  связь с контактом/компанией через `ContactsService` — см. `deals/README.md`).

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

## Как добавить новую фичу

Повтори анатомию `crm.contacts` (см. CLAUDE.md «Как добавить фичу»): самодостаточная
папка с `feature.toml`, зависимости только вниз (фича → ядро → shared),
горизонталь — только через публичные сервисы/события, чужие таблицы не читать.
