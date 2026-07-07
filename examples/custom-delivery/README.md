# Кейс: кастомная доставка для клиента

**Задача клиента.** На каждый оплаченный заказ завести доставку: адрес, статус
(`created → shipped → delivered`), трек-номер; персонал видит доставки в админке.

**Решение.** Новая фича `commerce.delivery` (в `modules/commerce/delivery/`),
которая слушает событие `commerce.order.paid` и заводит доставку. Ядро и
`commerce.orders` — без единой правки: расширение идёт «сбоку», через шину и
публичные интерфейсы. Ниже — диф по анатомии фичи, обоснование, чек-лист.

## Диф (ключевые файлы)

### `feature.toml`

```toml
name = "commerce.delivery"
description = "Доставка оплаченных заказов: адрес, статус, трек-номер."
requires_features = ["commerce.orders"]   # реагирует на его событие
requires_core = ["auth", "tenants"]
owns_tables = ["commerce_deliveries"]
publishes_events = ["commerce.delivery.shipped", "commerce.delivery.delivered"]
listens_events = ["commerce.order.paid"]  # ← явное имя (wildcard — привилегия ядра)
```

### `models.py` (эскиз)

```python
class Delivery(TimestampMixin, TenantScopedBase):
    __tablename__ = "commerce_deliveries"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    order_id: Mapped[UUID]          # by value — НЕ FK на commerce_orders (чужая фича)
    address: Mapped[str]
    status: Mapped[str] = mapped_column(default="created")  # created|shipped|delivered
    tracking_code: Mapped[str | None]
```

### `subscribers.py` — реакция на оплату заказа

```python
@bus.subscribe("commerce.order.paid", reliable=True)   # явное имя из listens_events
async def open_delivery(event: EventEnvelope) -> None:
    runtime = current_handler_runtime()
    order_id = UUID(event.payload["order_id"])
    await DeliveryService(runtime.uow, runtime.bus, runtime.ctx).open_for_order(order_id)
```

### `admin.py` — экран для персонала

```python
router = APIRouter()

@router.get("", dependencies=[Depends(require_permission("commerce.delivery:read"))])
async def list_deliveries(...) -> PageResult[DeliveryDTO]: ...

DELIVERY_SCREEN = AdminScreen(slug="deliveries", title_key="admin.screen.deliveries",
                              module="commerce.delivery", router=router,
                              permission="commerce.delivery:read")
```

### `__init__.py` — точки входа загрузчика

```python
def install() -> None:
    register_delivery_rbac()                       # commerce.delivery:read + гранты
    import modules.commerce.delivery.subscribers   # подписка на шину
    admin_registry.register(DELIVERY_SCREEN)       # экран в меню
```

Плюс `migrations/…commerce_delivery0001_deliveries.py` (ветка `commerce_delivery`,
таблица + `enable_tenant_rls`), `service.py`, `schemas.py`, `permissions.py`,
`README.md`, `tests/`.

## Почему так (объяснение решений)

- **Отдельная фича, не правка `orders`/ядра.** Доставка — независимая опция:
  одни клиенты её хотят, другие нет. Отдельная фича включается/выключается копией
  папки и не раздувает `orders`.
- **Связь через событие `commerce.order.paid`, а не чтение таблицы заказов.**
  Горизонталь фича→фича — только публичный интерфейс или шина (§1.1). `order_id`
  хранится значением, без FK на `commerce_orders` — чужие таблицы не читаются.
- **`reliable=True` подписчик.** Потеря «завести доставку» недопустима; шина даёт
  at-least-once + дедуп по `event_id`, обработчик идемпотентен (`open_for_order`
  не заводит вторую доставку на тот же заказ).
- **Свой RBAC-код и admin-экран.** Персонал видит доставки тем же механизмом, что
  и заказы; право `commerce.delivery:read` выдаётся owner/admin через
  `system_role_grants.extend`.
- **Своя ветка миграций с tenant-RLS.** Таблица тенантная, изоляция — как у всех.

## Чек-лист внедрения

- [ ] `feature.toml`: `requires_features`, `listens_events`, `owns_tables` честны
      (проверит `tests/test_manifest_honesty.py`).
- [ ] Таблица с `tenant_id` + `enable_tenant_rls` + тест изоляции тенантов.
- [ ] Каждый эндпоинт/ admin-роут несёт `require_permission`; негативный тест 403.
- [ ] Подписчик идемпотентен (повтор доставки события не плодит доставки).
- [ ] `install()` регистрирует RBAC + подписчик + admin-экран; `router` смонтирован
      загрузчиком.
- [ ] `README.md` фичи: интерфейс, события, подключение.
- [ ] `make lint` / `make test` зелёные; перенос `tools/add-feature commerce.delivery`
      тянет `commerce.orders` + `commerce.products`.
