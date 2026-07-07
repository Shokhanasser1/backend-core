# Чек-лист продакшен-развёртывания

Практическое руководство по выкатке backend-core (или собранного из него
клиентского проекта) в прод. Приложение **stateless** — всё состояние в Postgres
и Redis; конфигурация только через окружение (12-factor). Топология: `api`
(uvicorn), `worker` (arq), PostgreSQL 16, Redis 7.

## 1. Провижининг ролей БД (обязательно до миграций)

Роли — объекты кластера, не создаются миграциями (`shared/db_provisioning.py`).
В dev их ставит `docker/postgres-init/01-roles.sql`; в проде — вручную, с
**реальными паролями**, все `NOSUPERUSER NOBYPASSRLS NOINHERIT`:

| Роль | Назначение | Права |
|------|-----------|-------|
| `app_migrator` | владелец схемы, миграции | CREATE/USAGE на схему |
| `app_user` | рантайм api/worker | под RLS; per-table гранты из миграций |
| `app_maintenance` | кросс-тенантные джобы, стоки | обходит tenant-фильтр политиками |
| `app_retention` | ретенция журнала аудита | только SELECT/DELETE на `audit_log` |

Рендер DDL: `render_role_bootstrap_sql()` — но пароли обязательно свои.
`app_user` НЕ владеет таблицами и НЕ имеет BYPASSRLS — RLS применяется ко всем
рантайм-запросам (вторая линия изоляции тенантов).

## 2. Секреты и конфигурация

- Всё через env; в репозитории — только `.env.example` с фиктивными значениями.
  В CI работает gitleaks — секрет в коммите валит сборку.
- Обязательные секреты прода: `JWT_SECRET` (длинный случайный),
  `SECRET_ENCRYPTION_KEYS` (Fernet-ключи, base64; несколько через запятую —
  ротация MultiFernet: новый ключ первым, старый оставить для расшифровки),
  реальные `DATABASE_*_URL` (по роли), креды провайдеров/каналов
  (`PAYME_*`, `CLICK_*`, `SMTP_*`, `TELEGRAM_BOT_TOKEN`, `ESKIZ_*`).
- Хранить в секрет-менеджере (Vault / cloud secrets), не в файлах образа.
- `CORS_ORIGINS` — строгий белый список; пусто = кросс-домен запрещён.
- `ENABLED_PAYMENT_PROVIDERS` — включать только провайдеров с настроенными
  кредами (провайдер без кредов валит старт намеренно).

## 3. Миграции

- Применять как `app_migrator`: `python -m migrations.cli upgrade heads`
  (именно `heads` — мультиветочный layout). В compose это делает entrypoint `api`.
- Все миграции обратимы (есть рабочий `downgrade`); прогон вперёд/назад — в CI.
- Порядок: провижининг ролей → `upgrade heads` → старт api/worker.

## 4. TLS

- TLS терминируется на реверс-прокси/ingress перед `api` (сам uvicorn — за прокси).
- Приложение уже шлёт security-headers (HSTS, X-Content-Type-Options,
  X-Frame-Options, Referrer-Policy — `app/middleware.py`); HSTS имеет смысл только
  под HTTPS.
- Прокси должен пробрасывать реальный клиентский IP (для аудита `ip`) и
  `X-Request-ID` при наличии.

## 5. Бэкапы БД и проверка восстановления

- Регулярный `pg_dump` (или PITR через WAL-archiving для точки во времени).
- **Проверять восстановление**, а не только снятие: периодический тестовый
  restore в отдельную БД + smoke (`alembic current`, выборка). Бэкап без
  проверенного restore — не бэкап.
- В бэкап входит и `audit_log` (append-only журнал — юридически значимая история;
  ретенция чистит его старше горизонта, бэкап хранит дольше при необходимости).
- Хранить бэкапы в том же юрисдикционном контуре (см. §7).

## 6. Размещение данных в УЗ (ЗРУ о персональных данных)

- Персональные данные граждан УЗ хранятся на серверах в Узбекистане (локализация
  хранения). Постгрес и Redis (а значит и все ПД: email/телефон в `users`,
  `notification_outbox`, `audit_log.ip`) — в дата-центре в УЗ.
- Приложение stateless → переносимо; юрисдикция определяется размещением Postgres/
  Redis/бэкапов. Внешние вызовы (Payme/Click/Eskiz/Telegram) — локальные для рынка.
- Ретенция ПД включена: терминальные строки `notification_outbox` (recipient —
  email/телефон) чистятся (`NOTIFICATION_RETENTION_DAYS`, дефолт 90);
  `audit_log` — `AUDIT_RETENTION_DAYS` (дефолт 730). См. §8.

## 7. Наблюдаемость

- `SENTRY_DSN` — включает Sentry (PII-scrubbing включён всегда; пусто = выключен).
- `/health` (liveness), `/ready` (проверяет Postgres+Redis — вешать на readiness-
  probe), `/metrics` (Prometheus: латентность/коды HTTP, глубина очереди arq).
- JSON-логи structlog с `request_id`/`tenant_id`/`user_id`; секреты/токены/ПД
  маскируются. Собирать централизованно.

## 8. Воркер и фоновые джобы (arq)

`worker` обязателен (иначе события шины, уведомления и ретенция не идут). Cron
(`app/worker.py`): `expire_checkouts` (5 мин), `dispatch_notifications` (15 с),
`purge_retention` (03:00 — `audit_log` как `app_retention`; `processed_events` и
PII-строки `notification_outbox` как `app_maintenance`). Воркеру нужен
`DATABASE_RETENTION_URL`. Настройки ретенции: `AUDIT_RETENTION_DAYS`,
`NOTIFICATION_RETENTION_DAYS`, `PROCESSED_EVENTS_RETENTION_DAYS`.

## 9. Обновление зависимостей

- Пины — `uv.lock` (в CI `uv lock --check`). Сканы в CI: pip-audit, bandit,
  gitleaks, trivy (образ). Security-фиксы приоритетны, помечаются `[SECURITY]`
  в CHANGELOG.
- Обновлять зависимости регулярно, прогонять полный CI (lint + mypy + тесты +
  сканы) перед выкаткой.

## 10. Обновление шаблона в клиентских проектах

Клиентские проекты собраны из этого шаблона; как до них доносить фиксы ядра —
`docs/UPDATE.md` и ADR-0009 (стратегия обновления, template-drift джоба в CI).

## Короткий чек-лист перед первым деплоем

- [ ] Роли БД созданы с реальными паролями (NOSUPERUSER, NOBYPASSRLS).
- [ ] Секреты в секрет-менеджере; `JWT_SECRET`, `SECRET_ENCRYPTION_KEYS` заданы.
- [ ] `DATABASE_*_URL` (4 роли), `DATABASE_RETENTION_URL` для воркера.
- [ ] `upgrade heads` прошёл как `app_migrator`.
- [ ] TLS на прокси; проброс IP и security-headers работают.
- [ ] Бэкап настроен И проверен тестовым restore.
- [ ] Postgres/Redis/бэкапы — в УЗ.
- [ ] Sentry DSN, метрики и логи собираются; `/ready` зелёный.
- [ ] `worker` запущен; cron-джобы идут (уведомления уходят, ретенция чистит).
- [ ] `CORS_ORIGINS`, `ENABLED_PAYMENT_PROVIDERS` выставлены под клиента.
