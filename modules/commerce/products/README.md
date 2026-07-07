# commerce.products

## Назначение

Каталог товаров тенанта. Персонал управляет им через RBAC-эндпоинты; sibling-фичи
(cart, orders) узнают цену товара через публичный `ProductService`, не читая
таблицу. Независимая фича — переносится в проект в одиночку.

## Публичный интерфейс

- **`ProductService`**: `create`, `update`, `archive`, `get`, `list`,
  `get_sale_info(product_id) -> Money` (для cart/orders: цена активного товара).
- **DTO:** `ProductDTO`, `CreateProductIn`, `UpdateProductIn`.
- **Права:** `commerce.product:read` (owner/admin/member), `commerce.product:create`,
  `commerce.product:update` (owner/admin).
- **События:** `commerce.product.created|updated|archived` (payload: `product_id`).
- **Роуты:** `/api/commerce/products` (staff, RBAC).

## Манифест

`feature.toml`: `requires_core = ["auth", "tenants"]`, `owns_tables =
["commerce_products"]`, `requires_features = []`.

## Подключение в новый проект

1. Скопировать папку `modules/commerce/products/` (или `tools/add-feature` —
   тянет цепочку `requires`).
2. `ENABLED_MODULES=commerce` (иначе роуты и RBAC не поднимутся).
3. Миграции: `python -m migrations.cli upgrade heads` (ветка `commerce_products`
   создаёт `commerce_products` с tenant-RLS).

## Типовые кастомизации

- Мультиязычное `name` → `jsonb` + рендер по локали (как `plans.name`).
- Категории → отдельная фича, а не поля products. Изображения — отдельная фича
  `commerce.product_images` (поверх `core/files`).
