# commerce.orders

## Назначение

Заказы покупателя: оформление → оплата через billing → чек → admin-экран. Это
фича, которая прогоняет через себя всё ядро (§6.5): права, платежи, уведомления,
события, аудит, админку — строго через публичные интерфейсы, без правок ядра.
`requires commerce.products`.

## Публичный интерфейс

- **`OrderService`**: `place_order` (buyer), `get_own`/`list_own(page)` (buyer,
  ownership, пагинация), `list_all(page)` (staff; право проверяется и в сервисе),
  `mark_paid`/`cancel` (по событиям billing). Для соседней storefront-фичи
  (`commerce.cart`) публичен и FastAPI-dependency `order_service`.
- **DTO:** `OrderDTO`, `OrderItemDTO`, `OrderCheckoutDTO`, `PlaceOrderIn`.
- **Права:** `commerce.order:read` (owner/admin) — staff-просмотр.
- **События:** публикует `commerce.order.created|paid|canceled`; слушает
  `billing.payment.succeeded|failed|canceled|expired`.
- **Шаблон:** `commerce.order_paid` (ru/uz) — чек покупателю.
- **Роуты:** `/api/commerce/orders` (storefront; список — `PageResult`,
  `?limit=&offset=`); admin-экран `/api/admin/orders`.

## Как работает сквозной сценарий

1. `place_order` оценивает позиции через `ProductService.get_sale_info`, создаёт
   заказ (pending) и платёж через `PaymentService.create_payment`
   (`reference = order_id`), отдаёт checkout-URL.
2. Вебхук провайдера → billing финализирует платёж → `billing.payment.succeeded`.
3. Reliable-подписчик `mark_order_paid` помечает заказ оплаченным, публикует
   `commerce.order.paid` и шлёт чек через `NotificationService`.
4. Отказные исходы (`failed|canceled|expired`) → заказ `canceled`.
5. **Поздняя оплата.** Если провайдер подтвердил оплату уже после нашего истечения
   checkout (`billing.payment.succeeded` с `late: true`), заказ, отменённый ИМЕННО
   этим истечением этого же платежа, становится `paid` (событие `commerce.order.paid`
   с `late: true`, чек уходит): деньги списаны, резерва нет, строки — снимок цен.
6. audit пишет всю цепочку wildcard-подпиской; staff видит заказы в admin-экране.

## Манифест / подключение

`requires_features = ["commerce.products"]`,
`requires_core = ["auth","tenants","billing","notifications","admin"]`. Ветка миграций
`commerce_orders`. `ENABLED_MODULES=commerce` + включённый платёжный провайдер
(`ENABLED_PAYMENT_PROVIDERS`).

## Типовые кастомизации

- Резерв склада → слушать `commerce.order.paid` в фиче inventory.
- Оформление из корзины уже есть: `commerce.cart` вызывает `place_order` на чекауте.
