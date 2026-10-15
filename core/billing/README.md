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
- Джобы (`jobs.py`, cron воркера): `expire_stale_checkouts` (брошенный checkout →
  `expired`) и `expire_ended_subscriptions` (конец периода).

**Жизненный цикл подписки.** У тенанта не больше одной живой подписки
(`trialing|active|past_due`) и не больше одного платного checkout (`pending`) —
два частичных уникальных индекса. Апгрейд с free: платная `pending` живёт рядом с
free; успех платежа в одной транзакции закрывает free (`canceled`,
`billing.subscription.replaced`) и активирует платную. Повторный `start_subscription`
того же плана и провайдера возвращает тот же checkout; другой план/провайдер — 409,
пока открытый не оплачен или не истёк. Оплата того же плана заранее продлевает
период от текущего конца, а не с «сейчас». Неуспешный/отменённый/брошенный
checkout снимает `pending`, free остаётся. Конец периода: бесплатный план
продлевается, платный (автосписаний в v1 нет) закрывается (`expired`, либо
`canceled` при `cancel_at_period_end`, событие `billing.subscription.ended`) с
откатом на free-план (`BILLING_AUTO_SUBSCRIBE`).

**Гонки статусов.** Вебхук и expiry-джоба берут строку платежа `FOR UPDATE` до
чтения статуса: параллельный переход дожидается и перечитывается, а не
затирается. `expired` — наш маркер брошенного checkout, не вердикт провайдера:
поздний успех (`expired → succeeded`) принимается — деньги уже списаны.

**Вебхуки.** Сумма, которую не удалось разобрать, нормализуется в 0 и не
сверяется (ответ «неверная сумма», а не 500); действие без суммы (`None`) —
нечего сверять. Payme отвечает реальными `state`/`create_time`/`perform_time`/
`cancel_time`/`reason` из записанных переходов (повтор и `CheckTransaction` видят
те же значения); неизвестная транзакция → -31003. Click `Complete` с `error < 0` —
отмена (ответ -9), не подтверждение. Stripe: подпись с окном 300 с, создание
сессии — один вызов ~10 с без ретраев внутри запроса (держит DB-транзакцию;
повтор — клиентом с тем же ключом идемпотентности). Отклонённые по подписи
запросы журналируются с обрезанным телом (4 КиБ). `create_payment`: ключ
идемпотентности с другими параметрами → 409; гонка одинаковых ключей —
savepoint + чтение победителя; `return_url` — только абсолютный http(s) без `;`.

## Права (владеет)

`billing.plan:read`, `billing.subscription:read`, `billing.subscription:manage`.
Проверяются и в сервисе (`PermissionGuard`): тарифы, провайдеры, чтение/старт/
отмена подписки. `create_payment` и финализаторы `mark_*` — интерфейс для соседей,
вебхуков и джоб (покупатель оплачивает заказ без прав billing), без guard.

## События

- **Публикует:** `billing.payment.created|succeeded|failed|canceled|expired`,
  `billing.subscription.activated|canceled|replaced|ended`.
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
