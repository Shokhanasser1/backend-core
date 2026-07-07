# core/auth

## Назначение

Идентичность и доступ: регистрация, вход, JWT (access + refresh с ротацией),
2FA (TOTP + recovery-коды), смена/сброс пароля, и **RBAC** — реестр прав и их
проверка. Пользователи глобальны (один аккаунт на все тенанты, ОВ-01); роль и
права — в текущем тенанте (владеет связкой `core/tenants`).

## Публичный интерфейс

- **`AuthService`** (`service.py`): `register`, `authenticate`, `complete_two_factor`,
  `refresh`, `issue_tenant_token`, `logout`, `change_password`,
  `request_password_reset`, `reset_password`, `enable_totp`, `confirm_totp`,
  `disable_totp`, `get_user`.
- **`AccessService`** (`access_service.py`) — читающая сторона RBAC:
  `list_permissions`, `has_permission`, `require` (проверка на уровне сервиса).
  Права резолвит через `PermissionResolver` (реализует `core/tenants`).
- **`register_permissions(module, [...])`** — модули объявляют свои коды прав на
  старте; на нём же держится стартовая валидация роутов.
- **Маркеры эндпоинтов** (`deps.py`): `require_permission(code)`,
  `authenticated_endpoint(reason)`, `public_endpoint(reason)` +
  `ServiceBundle`/`authed_bundle`/`public_bundle` (request-scoped сервисы на одной
  транзакции).
- **`UserDirectory`** (`directory.py`): `get_contact(user_id) -> UserContact`
  (email/phone/locale) — для соседей (billing/notifications) без чтения таблиц auth.
- Роутер `/api/auth`.

## События (публикует)

`auth.user.registered`, `auth.user.login_succeeded`, `auth.user.login_failed`,
`auth.user.password_changed`, `auth.user.password_reset_requested`,
`auth.user.two_factor_enabled`, `auth.user.two_factor_disabled`.
Подписок нет.

## Права

Свои коды прав auth не вводит (доступ к своим действиям — через маркеры
`authenticated`/`public`). RBAC-механику предоставляет для всех модулей.

## Как расширять

- Новый флоу — метод в `AuthService` + роут в `router.py` с маркером.
- Примитивы безопасности изолированы в `security/` (argon2id, JWT со строгим
  alg, TOTP). JWT — HS256 (ADR-0010); менять alg только там.

## Не публично

Таблицы `users`/`user_totp`/`user_recovery_codes`/`refresh_tokens` (глобальные),
хеши и шифрованные секреты. Соседи читают только через `UserDirectory`.
