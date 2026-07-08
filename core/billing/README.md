# core/billing

## Назначение

Тарифы, подписки и платежи через общий порт `PaymentProvider` (адаптеры Payme,
Click и Stripe). Payme/Click работают по UZS; Stripe — для зарубежных клиентов
(мультивалютность, суммы 1:1 с minor units ledger). Валюты/планы — глобальные
справочники; подписки/платежи — тенантные, без DELETE (финансовая история).
Платежи идемпотентны, вебхуки переживают повторную доставку.

## Публичный интерфейс

- **`BillingService`** (`service.py`): `list_plans`, `get_subscription`,
  `start_subscription`, `cancel_subscription`, `auto_subscribe` (авто-подписка
  новых тенантов, ОВ-21).
- **`PaymentService`** (`service.py`): `list_providers`, `create_payment` (идемпотентно
  по `(tenant, idempotency_key)`), `get_payment`, статусная машина
  `mark_pending|succeeded|failed|canceled_by_provider|expired`, `cancel_payment`.
  Активация подписки — в той же транзакции, что и `succeeded`.
- **Порт `PaymentProvider`** (`ports.py`): `create_checkout`, `parse_webhook`,
  `build_webhook_response`. Адаптеры — `adapters/payme.py`, `adapters/click.py`,
  `adapters/stripe.py`, включаются `ENABLED_PAYMENT_PROVIDERS`. Payme/Click строят
  URL и принимают merchant-колбэки; Stripe создаёт Checkout Session серверным
  вызовом API (httpx + `call_resilient`) и принимает подписанные вебхуки
  (`checkout.session.completed`→confirm, `checkout.session.expired`→cancel,
  прочие типы событий → 200-ack без изменения состояния).
- **`WebhookProcessor`** (`webhooks.py`): `process` — проверка подписи → элевация
  system→tenant → идемпотентность `(provider, dedup_key)` + FOR UPDATE-replay →
  сверка суммы → ответ в диалекте провайдера.
- Роутеры: `/api/billing` (authed, `router` в `api.py`) и вебхуки (`router.py`, public).
- Джоба `expire_stale_checkouts` (`jobs.py`) — cron воркера.

## Права (владеет)

`billing.plan:read`, `billing.subscription:read`, `billing.subscription:manage`.

## События

- **Публикует:** `billing.payment.created|succeeded|failed|canceled|expired`,
  `billing.subscription.activated|canceled`.
- **Слушает:** `tenants.tenant.created` → авто-подписка (`subscribers.py`); чеки
  billing→notifications на `payment.succeeded`/`subscription.activated` (`receipts.py`).

## Как добавить провайдера

1. `adapters/<name>.py` реализует порт `PaymentProvider`.
2. Зарегистрируй в `adapters/__init__.py` (`build_payment_providers`).
3. Креды — из env (`.env.example`); включение — `ENABLED_PAYMENT_PROVIDERS`.
4. Негативные тесты вебхука (неверная подпись, чужой заказ, несовпадение суммы).

## Не публично

Таблицы `plans`/`subscriptions`/`payments`/`webhook_events`; ручная сверка —
`docs/RECONCILIATION.md`. Возврат денег в v1 — операция вне системы.
