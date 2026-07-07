# core/tenants

## Назначение

Организации (тенанты), участники (membership), роли и приглашения. Владеет
связкой «пользователь → тенант → роль → права» и системными ролями
owner/admin/member. Реализует `PermissionResolver` для `core/auth` (RBAC).

## Публичный интерфейс

- **`TenantService`** (`service.py`): `create_tenant`, `update_tenant`, `set_status`,
  `get_tenant`, `list_user_tenants`, `list_members`, `get_membership`,
  `has_active_membership`, `invite_member`, `revoke_invitation`, `accept_invitation`,
  `change_member_role`, `remove_member`, `get_permission_codes` (резолвер прав).
  Инвариант: у тенанта всегда есть хотя бы один owner.
- **`system_role_grants`** (`permissions.py`) — реестр дефолтных грантов системным
  ролям. Другие модули дописывают свои коды через `.extend({ROLE_OWNER: {...}})`;
  `sync.py` идемпотентно синкает результат в `role_permissions` на старте.
- **`TenantDirectory`** (`directory.py`): `get_owner_user_id`, `get_default_locale`
  — для соседей (billing/notifications) без чтения таблиц tenants.
- Роутер `/api/tenants`.

## Права (владеет)

`tenants.tenant:read|update`, `tenants.member:read|invite|remove|update_role`,
`tenants.role:read|manage`. Раскладка по системным ролям — в `permissions.py`.

## События (публикует)

`tenants.tenant.created`, `tenants.tenant.status_changed`, `tenants.member.invited`,
`tenants.member.joined`, `tenants.member.removed`, `tenants.member.role_changed`.

## Как расширять

- Кастомные роли тенанта — данные (`roles`/`role_permissions`), не код; системные
  роли (`is_system`) не меняются из admin-API.
- Модуль, которому нужен доступ к тарифам ролей, добавляет свои гранты через
  `system_role_grants.extend(...)` в своём `register_<module>_rbac()` — tenants при
  этом не импортирует чужой модуль.

## Не публично

Таблицы `tenants`/`memberships`/`roles`/`role_permissions`/`invitations` (RLS §3.3).
Соседи читают только через `TenantDirectory` или `AccessService`.
