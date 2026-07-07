# core/notifications

## Назначение

Отправка уведомлений через общий порт `NotificationChannel` (Telegram, Eskiz SMS,
email/SMTP) с очередью-outbox: надёжная доставка, ретраи с backoff, dead-letter,
дедупликация, шаблоны ru/uz. Инфраструктурный слой — семантику писем задаёт
модуль-владелец (напр. billing), notifications лишь рендерит и доставляет.

## Публичный интерфейс

- **`NotificationService`** (`service.py`): `send` (получатель — user или адрес,
  цепочка локали, идемпотентность по `dedup_key`), `get_status`,
  `set_channel_config` (write-only, ОВ threat V10), `get_channel_status` (маска).
- **`register_templates(module, [...], dir)`** (`registry.py`) — модули объявляют
  свои шаблоны на старте (симметрично `register_permissions`). Файлы —
  `templates/<locale>/<key>.txt`; обе локали (ru, uz) обязательны, парити
  проверяется на старте.
- **Порт `NotificationChannel`** (`ports.py`): `configured`, `send`. Каналы —
  `channels/{telegram,eskiz,email}.py`, dormant-by-default (нет кредов → no-op),
  с circuit breaker и маскированием адресов.
- Диспетчер outbox `dispatch_due_notifications` (`dispatcher.py`) — cron воркера
  (`SELECT FOR UPDATE SKIP LOCKED` + lease, backoff, dead-letter).

## События

- **Публикует:** `notifications.message.sent` (in-process, не пишется в audit),
  `notifications.message.failed` (dead-letter).
- **Слушает:** платёжные чеки живут в billing (`core/billing/receipts.py`), а не
  здесь — когезия: billing владеет семантикой и шаблонами.

## Права / роуты

HTTP-роутов и своих кодов прав нет: управление каналами — через admin (Фаза 4+)
и `set_channel_config`. Платформенные отправки (верификация email, сброс пароля)
идут без тенанта через платформенные креды (`SMTP_*`, `TELEGRAM_BOT_TOKEN`, `ESKIZ_*`).

## Как добавить

- **Канал:** новый `channels/<name>.py` реализует `NotificationChannel`; включи в
  `build_notification_channels`. Внешние вызовы — с таймаутом/ретраями/circuit
  breaker; адреса маскируются в логах и событиях.
- **Шаблон:** `register_templates(...)` + файлы для КАЖДОЙ локали (иначе старт
  падает). Рендер — `string.Template` (`$var`).

## Не публично

Таблицы `notification_settings` (config шифрован) и `notification_outbox`
(содержит `recipient` — ПД; терминальные строки чистит ретенция, §2.4).
