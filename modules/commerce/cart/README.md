# commerce.cart

## Назначение

Корзина покупателя (storefront). Опциональная фича: `requires commerce.products`
(цены берёт через `ProductService`). Покупатель — аутентифицированный пользователь,
не член тенанта (ОВ-39): доступ через `authenticated_endpoint`, магазин — из
заголовка `X-Shop-Tenant`, принадлежность корзины — по `customer_user_id` в сервисе.

## Публичный интерфейс

- **`CartService`**: `add_item`, `remove_item`, `get_cart`, `checkout`.
- **DTO:** `CartDTO`, `CartItemDTO`, `AddItemIn`.
- **События:** `commerce.cart.checked_out` (`cart_id`, `customer_user_id`,
  `total_amount`, `currency`).
- **Роуты:** `/api/commerce/cart` (storefront; заголовок `X-Shop-Tenant` обязателен).
- Своих прав/шаблонов/admin-экранов нет.

## Манифест

`requires_features = ["commerce.products"]`, `owns_tables = ["commerce_carts",
"commerce_cart_items"]`. Без products фича не устанавливается (старт падает с
понятной ошибкой).

## Подключение

1. `tools/add-feature commerce.cart` — потянет `commerce.products` в придачу.
2. `ENABLED_MODULES=commerce`, `upgrade heads` (ветка `commerce_cart`).

## Типовые кастомизации

- Купоны/скидки → поле/пересчёт в `checkout`.
- Резерв склада на чекауте → слушать `commerce.cart.checked_out` в фиче inventory.
