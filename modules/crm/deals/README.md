# crm.deals

Сделки и воронка: opportunities, движущиеся по настраиваемым стадиям. Вторая фича
модуля `crm`. Зависит от `crm.contacts` (`requires_features = ["crm.contacts"]`):
сделка опционально связана с контактом и/или компанией, которые валидируются через
публичный `ContactsService` — **чужие таблицы не читаются**.

## Назначение

Хранит сделку тенанта: `title`, `stage`, сумму (`amount` в минимальных единицах +
`currency`), опциональные ссылки `contact_id`/`company_id`, `notes`.

**Стадии воронки — конфигурация клиента** (`CRM_DEAL_STAGES`), не жёстко зашитый
список: шаблон не знает воронку конкретного клиента.

```dotenv
CRM_DEAL_STAGES=lead,qualified,proposal,won,lost
```

Пусто = сделки непригодны (create падает `422`). Стадия сделки обязана быть из
набора; создание без явной стадии кладёт сделку на **первую** стадию.

## Публичный интерфейс

`DealService` (реэкспорт в `deals/__init__.py`):

- `create(title, amount, currency=, stage=None, contact_id=None, company_id=None, notes=None)`
  — стадия по умолчанию = первая сконфигурированная; ключи связей валидируются.
- `update(deal_id, …)` — PATCH-семантика (`None` = не менять).
- `get(deal_id)` / `list(page, stage=, contact_id=, company_id=)` / `delete(deal_id)`.

Суммы — целые минимальные единицы + `currency` (`shared/money.py`), валюта
проверяется по `CurrencyRegistry` (без `float`). Связи — «голые» Uuid, проверка
существования (в том же тенанте) через `ContactsService.get_contact/get_company`
(чужой/несуществующий id → `404`).

## Роуты

`GET|POST /api/crm/deals`, `GET|PATCH|DELETE /api/crm/deals/{id}`. Список принимает
фильтры `?stage=`, `?contact_id=`, `?company_id=`.

## Права

`crm.deal:{read,create,update,delete}`. Owner/admin — всё; member (менеджер) —
read/create/update, но **не delete** (обязательный негатив: member → `403` на DELETE).

## Таблица

`crm_deals` — тенантная (RLS, ветка Alembic `crm_deals`). FK `tenant_id → tenants`
(`ON DELETE RESTRICT`); `contact_id`/`company_id` — без межфичевого FK (валидация в
сервисе). `CHECK (amount >= 0)`.

## События

Публикует `crm.deal.{created,updated,deleted}` и **`crm.deal.stage_changed`**
(`{deal_id, from, to}` — когда update меняет стадию; полезно для автоматизаций).
Ничего не слушает.

## Перенос в клиентский проект

```bash
python -m tools.add_feature crm.deals /path/to/target
```

`tools/add-feature` тянет и зависимость `crm.contacts`. Затем `ENABLED_MODULES=crm`,
задать `CRM_DEAL_STAGES` и `python -m migrations.cli upgrade heads`.
