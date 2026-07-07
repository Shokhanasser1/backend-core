# core/admin

## Назначение

Admin-каркас: авторизация, права и реестр admin-экранов. Своей бизнес-логики и
таблиц нет (схема §2.6) — это механизм, которым модули и фичи подключают свои
admin-экраны, гейтенные тем же RBAC, что и всё остальное.

## Публичный интерфейс

- **`AdminScreen`** (`registry.py`) — dataclass: `slug` (сегмент URL), `title_key`
  (i18n), `module` (владелец, для диагностики), `router` (эндпоинты экрана),
  `permission` (право на видимость в меню).
- **`admin_registry.register(screen)`** — модуль/фича регистрирует экран на старте
  (дубль slug → ошибка старта).
- **`AdminService.screens_for(user_id)`** (`service.py`) — меню: только экраны, на
  которые у пользователя есть право в текущем тенанте.
- Роутер `/api/admin`: `GET /api/admin/screens` (право `admin.screen:read`) — меню.
- Монтирование экранов под `/api/admin/{slug}` — в композиционном корне
  (`app/admin_screens.py`); каждый admin-роут обязан нести ровно `require_permission`
  (валидация §5.4, `app/startup_checks.py: validate_admin_routes`).

## Права (владеет)

`admin.screen:read` (owner/admin) — право читать меню. Право на КАЖДЫЙ экран
объявляет его модуль-владелец (напр. `audit.record:read`), не admin.

## Как добавить экран

1. В модуле-владельце `admin.py`: собери `AdminScreen(slug=..., router=...,
   permission="<module>.<resource>:read", ...)` и `admin_registry.register(...)`.
   У каждого эндпоинта роутера — свой `require_permission` (`authenticated`/`public`
   в админке запрещены).
2. Добавь `import <module>.admin` в `core/admin/screens.py` (для ядра) — оно
   импортируется композиционным корнем на старте.
3. Готово: экран смонтируется под `/api/admin/{slug}` и попадёт в меню тем, у кого
   есть его `permission`. Пример — `core/audit/admin.py`.

Выключенный модуль не импортируется → его экранов физически нет.

## Не публично

Монтирование роутеров и внутренности реестра. Модуль трогает только `AdminScreen`
+ `admin_registry.register`.
