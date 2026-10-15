# commerce.cart

## Назначение

Корзина покупателя (storefront). Опциональная фича: `requires commerce.products`
(цены берёт через `ProductService`) и `commerce.orders` (чекаут создаёт заказ через
`OrderService`). Покупатель — аутентифицированный пользователь,
не член тенанта (ОВ-39): доступ через `authenticated_endpoint`, магазин — из
заголовка `X-Shop-Tenant`, принадлежность корзины — по `customer_user_id` в сервисе.

## Публичный интерфейс

- **`CartService`**: `add_item`, `remove_item`, `get_cart`, `checkout(provider)`.
- **DTO:** `CartDTO`, `CartItemDTO`, `AddItemIn`, `CheckoutIn`; чекаут отвечает
  `OrderCheckoutDTO` фичи orders (`order_id`, `payment_id`, `checkout_url`).
- **События:** `commerce.cart.checked_out` (`cart_id`, `customer_user_id`, `order_id`).
- **Роуты:** `/api/commerce/cart` (storefront; заголовок `X-Shop-Tenant` обязателен).
- Своих прав/шаблонов/admin-экранов нет.

## Поведение

- **Чекаут = заказ.** `POST /checkout {provider}` передаёт позиции в
  `OrderService.place_order`: цены пересчитываются по ТЕКУЩЕМУ прайсу, создаётся
  заказ и платёж, ответ — ссылка на оплату (201). Корзина закрывается только при
  успехе; любая ошибка (неизвестный провайдер, архивный товар) откатывает запрос —
  корзина остаётся как была.
- **Одна активная корзина** на покупателя в магазине (частичный уникальный индекс,
  миграция `commerce_cart0002`; старые дубли закрыты статусом `abandoned`).
  Параллельные «первые добавления» сходятся в одну корзину, изменения корзины идут
  под блокировкой её строки.
- **Не больше 1000 штук одного товара** в корзине (422, а не переполнение колонки).

## Манифест

`requires_features = ["commerce.products", "commerce.orders"]`, `owns_tables =
["commerce_carts", "commerce_cart_items"]`. Без них фича не устанавливается (старт
падает с понятной ошибкой).

## Подключение

1. `tools/add-feature commerce.cart` — потянет `commerce.products` и `commerce.orders`.
2. `ENABLED_MODULES=commerce`, `upgrade heads` (ветка `commerce_cart`).

## Типовые кастомизации

- Купоны/скидки → пересчёт в `OrderService.place_order` (цена считается там).
- Резерв склада на чекауте → слушать `commerce.cart.checked_out` в фиче inventory.
