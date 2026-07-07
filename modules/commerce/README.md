# Модуль commerce

Первый бизнес-модуль шаблона — витрина того, как из фич собирается модуль поверх
ядра (auth, tenants, billing, notifications, audit, admin). Включается конфигом
`ENABLED_MODULES=commerce`; выключен — его роутов и RBAC в приложении нет (таблицы
существуют, если папки фич на диске: миграции привязаны к наличию папки, а не к
флагу).

## Фичи

| Фича | Назначение | Требует |
|------|-----------|---------|
| `commerce.products` | Каталог товаров (staff, RBAC) | — |
| `commerce.cart` | Корзина покупателя (storefront) | `products` |
| `commerce.orders` | Заказы: оплата, чек, admin-экран | `products` |

Карта зависимостей — DAG: `cart` и `orders` независимы друг от друга, обе тянут
`products`. Storefront-фичи (`cart`, `orders`) обслуживают покупателя —
аутентифицированного пользователя, не члена тенанта (ОВ-39): доступ через
`authenticated_endpoint`, магазин из заголовка `X-Shop-Tenant`, принадлежность
объекта проверяется в сервисе.

## Рецепты сборки

- **Каталог для персонала:** `products`.
- **Минимальный магазин:** `products + orders` (покупатель оформляет заказ и
  платит; корзины нет).
- **Магазин с корзиной:** `products + cart + orders`.

Перенос в клиентский проект — `tools/add-feature`, который тянет цепочку `requires`:

```bash
python -m tools.add_feature commerce.orders /path/to/target   # скопирует products + orders
```

Затем в целевом проекте: `ENABLED_MODULES=commerce` и
`python -m migrations.cli upgrade heads`.

## Публичные интерфейсы фич (для соседей)

- `commerce.products` → `ProductService` (реэкспорт в `products/__init__.py`):
  `get_sale_info(product_id) -> Money` — цена активного товара; `cart`/`orders`
  зовут его, не читая таблицу `commerce_products`.

## События модуля

`commerce.product.created|updated|archived`, `commerce.cart.checked_out`,
`commerce.order.created|paid|canceled`. Полные payload — в `feature.toml` каждой
фичи и в `docs/phase0/02-interfaces-events.md` §6.4.

## Как добавить новую фичу

1. Папка `modules/commerce/<feature>/` по анатомии (мастер-промпт §АНАТОМИЯ ФИЧИ):
   `feature.toml`, `models.py`, `schemas.py`, `service.py`, `router.py`,
   опц. `admin.py`, `subscribers.py`, `permissions.py`, `migrations/`, `tests/`,
   `README.md`.
2. `__init__.py` реэкспортирует публичный интерфейс + опц. `install()` и `router`
   (их зовёт/монтирует загрузчик `app/features.py`).
3. Зависимости только вниз (фича → ядро → shared); к соседям — только через их
   публичный интерфейс/события, объявленные в `requires_features`/`listens_events`.
   Честность манифеста проверяет `tests/test_manifest_honesty.py`.
4. Миграция — своя ветка Alembic (`branch_labels=("commerce_<feature>",)`,
   `depends_on` = tenants/shared), с tenant-RLS через `enable_tenant_rls`.
5. Пример подключения кастомной фичи — `examples/custom-delivery/`.
