# crm.contacts

CRM-адресная книга: люди (**contacts**) и организации (**companies**). Первая,
фундаментальная фича модуля `crm` — на неё опираются будущие `deals` и `tasks`.
Независимая фича (`requires_features = []`), опирается на ядро `auth` + `tenants`,
о модуле `commerce` ничего не знает.

## Назначение

Хранит две связанные сущности одного тенанта:

- **company** — организация (`name`, `website`, `industry`, `notes`);
- **contact** — человек (`first_name`, `last_name`, `email`, `phone`, `position`,
  `notes`), опционально привязанный к company того же тенанта (`company_id`).

Связь `contact → company` — внутрифичевый FK с `ON DELETE SET NULL`: удаление
компании **отвязывает** её контакты (не блокирует удаление, контакты остаются).
Существование `company_id` проверяется через тенант-скоупный репозиторий, поэтому
чужой/несуществующий id — это `404` на границе сервиса, никогда не утечка.

## Публичный интерфейс

`ContactsService` (реэкспорт в `contacts/__init__.py`) — соседи (`deals`, `tasks`)
читают людей/компании ЧЕРЕЗ него, не трогая таблицы фичи:

- Компании: `create_company` / `update_company` / `get_company` /
  `list_companies(page)` / `delete_company`.
- Контакты: `create_contact` / `update_contact` / `get_contact` /
  `list_contacts(page, company_id=None)` / `delete_contact`.

**PATCH-семантика** (общая по шаблону, как у `commerce.products`): в Update-методах
поле со значением `None` означает «не менять». Очистка поля обратно в `null` в v1
не выражается; переназначить контакт на другую компанию можно, отвязать — удалив
компанию (FK SET NULL).

## Роуты

- `GET|POST /api/crm/companies`, `GET|PATCH|DELETE /api/crm/companies/{id}`.
- `GET|POST /api/crm/contacts`, `GET|PATCH|DELETE /api/crm/contacts/{id}`.
  Список контактов принимает фильтр `?company_id=<uuid>`.

## Права

`crm.company:{read,create,update,delete}` + `crm.contact:{read,create,update,delete}`.
Раскладка по системным ролям: **owner/admin** — всё; **member** (менеджер по
продажам) — read/create/update людей и компаний, но **не delete** (удаление —
задача владельца организации). Отсюда обязательный негатив-тест: member получает
`403` на `DELETE`.

## Таблицы

- `crm_companies` — тенантная (RLS, ветка Alembic `crm_contacts`).
- `crm_contacts` — тенантная (та же ветка), FK `company_id → crm_companies`
  (`ON DELETE SET NULL`), FK `tenant_id → tenants` (`ON DELETE RESTRICT`).

## События

Публикует `crm.company.{created,updated,deleted}` и
`crm.contact.{created,updated,deleted}` (payload — идентификатор). Ничего не слушает.

## Перенос в клиентский проект

```bash
python -m tools.add_feature crm.contacts /path/to/target
```

Затем `ENABLED_MODULES=crm` и `python -m migrations.cli upgrade heads`.
