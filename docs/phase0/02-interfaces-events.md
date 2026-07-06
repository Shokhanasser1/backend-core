# Фаза 0 — Публичные интерфейсы ядра и события шины

> Статус: утверждена владельцем 2026-07-06 (реестр решений — `00-open-questions.md`;
> решённые пункты §7 аннотированы). Часть комплекта Фазы 0
> (схема БД, threat model и стратегия обновления — отдельные документы).
> Кода нет — только контракты. Все сигнатуры — эскизы уровня «что и с какими
> типами», не финальный код.

---

## 1. Принципы

### 1.1. Направление зависимостей

```
фича (modules/*/  Фаза 6+)  →  ядро (core/*)  →  shared/
```

- **Только «вниз».** Фича импортирует ядро и shared. Ядро импортирует shared.
  shared не импортирует ничего из проекта. Ядро никогда не импортирует модули.
- **Горизонталь** (фича → фича, модуль → модуль, core-модуль → core-модуль) —
  только двумя способами:
  1. вызов **публичного сервисного интерфейса** (раздел 3);
  2. подписка на **события шины** (раздел 6).
- **Чтение чужих таблиц запрещено всегда.** Ни ORM-моделей чужого модуля, ни
  raw SQL по чужим таблицам, ни «временно». Нужны данные — зови сервис или
  слушай событие.
- Каждая горизонтальная связь фичи декларируется в `feature.toml`
  (`requires_features`, `requires_core`, `publishes_events`, `listens_events`)
  и проверяется на старте приложения и тестом в CI. В `listens_events` —
  только явные имена событий; wildcard-подписка (`*`, префиксы) — привилегия
  core-модулей (v1: только audit, см. 2.6 и 3.5).

### 1.2. Что считается публичным интерфейсом модуля

Публично (и только это):

| Что | Где живёт |
|---|---|
| Сервисные классы и их методы, перечисленные в разделе 3 | реэкспорт в `core/<module>/__init__.py` |
| DTO/схемы, фигурирующие в сигнатурах этих методов | `core/<module>/schemas.py` |
| Доменные ошибки, которые эти методы поднимают | наследники `shared.errors.DomainError` |
| События, которые модуль публикует (раздел 6) | имена + payload зафиксированы здесь |
| FastAPI-зависимости, явно объявленные публичными (`require_permission`, `public_endpoint`, `authenticated_endpoint`, `current_user`, `current_tenant`) | `core/auth/deps.py`, `core/tenants/deps.py` |

Внутренности (запрещено использовать снаружи модуля):

- ORM-модели и таблицы (`models.py`), репозитории;
- роутеры и обработчики вебхуков;
- адаптеры провайдеров (Payme, Click, Eskiz, Telegram, SMTP);
- утилиты хеширования, подписи токенов, рендеринг шаблонов, rate limiter;
- всё, что не реэкспортировано в `__init__.py` модуля.

Правило enforcement: `__init__.py` каждого core-модуля реэкспортирует ровно
публичный интерфейс; тест честности манифестов (Фаза 6) и import-linter-правило
(Фаза 1) бьют по импортам мимо `__init__.py`.

---

## 2. Примитивы shared/

### 2.1. Контекст тенанта и актор

```python
# shared/context.py
@dataclass(frozen=True, slots=True)
class Actor:
    kind: Literal["user", "system", "integration"]
    id: str | None            # user_id | имя воркера/джобы | код провайдера ("payme")

@dataclass(frozen=True, slots=True)
class RequestContext:
    ip: str | None
    user_agent: str | None    # request_id не дублируется — он в TenantContext

@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID | None    # None — user-скоуп и system-пути (см. ниже)
    actor: Actor
    request_id: str | None
    locale: str
```

`TenantContext` создаётся:

- **API-запрос** — middleware из JWT. Механизм попадания `tenant_id` в
  контекст (клейм в токене vs заголовок) — Открытый вопрос №9;
- **arq-джоба-подписчик** — диспетчером шины из конверта события
  (`tenant_id`, `actor` восстанавливаются из `EventEnvelope`);
- **вебхуки и платформенные джобы** — system-контекст
  (`actor.kind="system"|"integration"`, `tenant_id=None`) — **только на чтение
  и только до идентификации объекта**.

**Повышение контекста (system → tenant) — единственный write-путь вебхуков и
платформенных джобов.** System-контекст бизнес-данные не пишет. Пример —
вебхук Payme: адаптер верифицирует подпись; billing находит платёж через
`SystemRepository` (system-контекст, read-only); затем открывает **новый**
`TenantContext(tenant_id=payment.tenant_id, actor=Actor("integration", "payme"))`
и выполняет финализацию платежа в нём — записи проходят автофильтр Repository
и RLS штатно, а `Service.emit` собирает конверт с корректным `tenant_id`.
Тот же паттерн у платформенных свипов (протухание платежей, продление
подписок): нашёл строку в system-контексте → обработал её в tenant-контексте
этой строки.

`tenant_id is None` легален ровно в двух случаях: user-скоуп (запрос
аутентифицирован, но вне тенанта — например, создание организации) и
system-пути (read-only, см. выше). Контекст же выставляет
`SET LOCAL app.tenant_id` для RLS (детали — в документе схемы БД).

### 2.2. Repository — автофильтрация по tenant_id

```python
# shared/repository.py
class Repository[M: TenantScopedBase]:
    """Все запросы автоматически дополняются WHERE tenant_id = ctx.tenant_id.
    INSERT автоматически проставляет tenant_id из контекста.
    Обойти фильтр из наследника невозможно — фильтр вшит в единственную
    точку построения запросов."""

    model: type[M]

    def __init__(self, session: AsyncSession, ctx: TenantContext) -> None: ...

    async def get(self, entity_id: UUID) -> M | None: ...
    async def get_or_raise(self, entity_id: UUID) -> M: ...        # NotFoundError
    async def find(
        self,
        *where: ColumnElement[bool],
        order_by: Sequence[ColumnElement[Any]] | None = None,
        page: Page | None = None,
    ) -> Sequence[M]: ...
    async def find_paged(
        self,
        *where: ColumnElement[bool],
        order_by: Sequence[ColumnElement[Any]] | None = None,
        page: Page,
    ) -> PageResult[M]: ...    # items + total; единственный штатный источник PageResult
    async def count(self, *where: ColumnElement[bool]) -> int: ...
    async def add(self, entity: M) -> M: ...
    async def delete(self, entity: M) -> None: ...
```

- Сервисные `list_*` методы (3.2 `list_members`, 3.5 `search`) строятся на
  `find_paged` — вручную клеить `find` + `count` не нужно.
- Модели без тенанта (глобальные справочники `currencies`, `plans`; глобальные
  `users`) наследуют `GlobalBase` и работают через `GlobalRepository[M]` —
  без автофильтра, но и без права писать tenant-данные.
- `SystemRepository[M]` — доступ без tenant-фильтра для внутренних нужд ядра
  (поиск платежа по вебхуку до установления тенанта). Живёт в shared, но
  импорт разрешён **только из core/** — правило import-linter; фичам запрещён.
  Write-путь после такого поиска — повышение контекста (2.1), не запись из
  system-контекста.
- RLS в Postgres — вторая линия: даже баг в репозитории не отдаст чужого
  тенанта.

```python
# shared/pagination.py
@dataclass(frozen=True, slots=True)
class Page:
    limit: int = 50
    offset: int = 0
    # __post_init__: 1 <= limit <= 200, offset >= 0, иначе ValueError —
    # лимит enforced конструктором, а не доверием к вызывающему

@dataclass(frozen=True, slots=True)
class PageResult[T]:
    items: Sequence[T]
    total: int
    limit: int
    offset: int
```

### 2.3. Service и границы транзакций

```python
# shared/service.py
class Service:
    """База сервисов. Держит UnitOfWork; события копятся и публикуются
    ТОЛЬКО после успешного commit (post-commit hook). Rollback — события
    отбрасываются, «призрачных» событий не бывает."""

    def __init__(self, uow: UnitOfWork, bus: EventBus, ctx: TenantContext) -> None: ...

    def emit(self, name: str, payload: Mapping[str, Any], *, version: int = 1) -> UUID:
        """Поставить событие в очередь текущей транзакции (конверт соберётся
        из ctx; event_id назначается сразу и возвращается — им же связывается
        прямая запись в audit, см. 3.5). Публикация — после commit."""

class UnitOfWork(Protocol):
    session: AsyncSession
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc: object) -> None: ...   # commit / rollback
    def on_commit(self, cb: Callable[[], Awaitable[None]]) -> None: ...
```

Публикация из arq-контекста: диспетчер шины оборачивает каждый
reliable-обработчик в его собственный `UnitOfWork` + `TenantContext`
(восстановленный из конверта). Поэтому обработчик-подписчик публикует события
тем же `Service.emit` — после commit его собственной транзакции. Отдельного
«режима публикации без транзакции» нет.

### 2.4. Доменные ошибки (вместо Result)

Решение: **типизированные исключения, а не Result-обёртка.** Обоснование:
FastAPI-хендлер один раз маппит иерархию на HTTP-ответы и i18n-тексты;
Result в Python без checked-типов даёт `unwrap`-шум и теряется в стеке
вызовов сервис→сервис. Ошибка порта внешней системы — тоже исключение.
Это расхождение с мастер-промптом (там в shared/ назван `Result`) —
вынесено на решение владельца: Открытый вопрос №8.

```python
# shared/errors.py
class DomainError(Exception):
    code: ClassVar[str]              # машиночитаемый, напр. "not_found"
    message_key: ClassVar[str]       # ключ i18n-каталога
    http_status: ClassVar[int]

class NotFoundError(DomainError):            ...   # 404
class ConflictError(DomainError):            ...   # 409 (дубль, гонка уникальности)
class InvariantViolationError(DomainError):  ...   # 422 (бизнес-правило; не путать с Pydantic-валидацией границы)
class PermissionDeniedError(DomainError):    ...   # 403
class AuthenticationError(DomainError):      ...   # 401
class RateLimitedError(DomainError):         ...   # 429

class ExternalServiceError(DomainError):     ...   # 502/503; база ошибок портов
class PaymentProviderError(ExternalServiceError): ...
class NotificationChannelError(ExternalServiceError): ...
class WebhookVerificationError(DomainError): ...   # 403 — только fallback; штатно транслируется в диалект провайдера (4.1)
class CircuitOpenError(ExternalServiceError): ...  # брейкер открыт — fail fast
```

Модули наследуют свои конкретные ошибки от этих (например
`billing.errors.PaymentAlreadyFinalizedError(ConflictError)`); публичны только
те, что фигурируют в контрактах раздела 3.

Исключение из общего маппинга: роуты вебхуков платёжных провайдеров
**исключены из общего error-хендлера** — любой их исход отвечается в диалекте
провайдера через `build_webhook_response` (раздел 4.1).

### 2.5. Деньги

```python
# shared/money.py
@dataclass(frozen=True, slots=True)
class Money:
    amount: int          # минимальные единицы валюты; отрицательные запрещены в v1
    currency: str = "UZS"  # ISO 4217

class CurrencyRegistry:
    """Справочник currencies (code, exponent): UZS=0, USD=2.
    Глобальная таблица, сидится миграцией, read-only в рантайме.
    Загружается в память один раз на старте — поэтому exponent синхронный:
    валидация Money не тянет await и сессию БД."""
    async def load(self) -> None: ...                # вызывается на startup
    def exponent(self, code: str) -> int: ...        # NotFoundError для незнакомой валюты
```

Никаких float нигде: в БД, в payload событий, в API — только `int` minor units
+ `currency`.

### 2.6. Событийная шина

Контракт: **лёгкий in-process pub/sub; обработчики, требующие надёжности,
уходят фоново через arq.**

```python
# shared/events.py
@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: UUID                  # dedup-ключ; назначается в момент emit
    name: str                       # "billing.payment.succeeded"
    version: int                    # версия схемы payload, с 1
    occurred_at: datetime           # UTC
    tenant_id: UUID | None          # None — только платформенные события
    actor: Actor
    payload: Mapping[str, Any]      # только JSON-типы, см. правила ниже

EventHandler = Callable[[EventEnvelope], Awaitable[None]]

class EventBus(Protocol):
    async def publish(self, event: EventEnvelope) -> None: ...
    def subscribe(self, pattern: str, *, reliable: bool = False) -> Callable[[EventHandler], EventHandler]:
        """Декоратор. pattern — точное имя ("billing.payment.succeeded"),
        префикс ("billing.payment.*") или "*" (все события).
        Wildcard/префикс разрешены только core-модулям (v1: audit);
        фичи подписываются на явные имена из своего listens_events.
        reliable=False — in-process; reliable=True — через arq."""
```

Как обработчик декларирует надёжную доставку:

```python
# core/notifications/subscribers.py
@bus.subscribe("billing.payment.succeeded", reliable=True)
async def send_payment_receipt(event: EventEnvelope) -> None: ...
```

Семантика доставки:

| | `reliable=False` (in-process) | `reliable=True` (arq) |
|---|---|---|
| Когда выполняется | сразу после commit издателя, в том же процессе | arq-воркер, асинхронно |
| Гарантия | at-most-once | at-least-once (повторы: 5 попыток, экспоненциальный backoff, затем dead letter) |
| Ошибка обработчика | логируется + Sentry, издателя НЕ роняет | ретрай; после исчерпания — dead letter + метрика |
| Дедупликация | нет | встроена в шину: перед вызовом обработчика — INSERT `(handler, event_id)` в служебную таблицу `processed_events` в одной транзакции с UoW обработчика; дубль → no-op (таблица — дополнение к документу схемы БД) |
| Требование к обработчику | быстрый, без внешних вызовов | внешние side-эффекты идемпотентны своим механизмом (например, delivery-запись перед отправкой SMS): окно at-least-once между внешним вызовом и commit шина закрыть не может |
| Для чего | метрики, инвалидация кеша | уведомления, аудит, межмодульные реакции |

Топология процессов (важно для выбора `reliable`):

- подписки регистрируются на старте **каждого процесса** (web и arq-воркер) —
  регистрация обязана лежать в модуле, импортируемом обоими;
- **in-process подписчик видит только события, изданные в его процессе.**
  Событие, опубликованное из arq-джобы, in-process подписчики web-процесса
  не увидят никогда. Нужна доставка независимо от процесса издателя —
  только `reliable=True`;
- reliable-обработчик исполняется в собственном UoW + TenantContext из
  конверта (2.3) и может сам публиковать события через `emit`.

Правила payload:

- только JSON-типы. Сериализация зафиксирована: `datetime` → строка ISO 8601
  в UTC; `UUID` → каноничная строка; деньги — пара `amount: int` +
  `currency: str`. В таблицах раздела 6 типы указаны семантически
  (`UUID`, `datetime`) — на проводе это строки;
- никаких секретов: токены, пароли, коды 2FA, ссылки сброса пароля — **никогда**
  (если уведомлению нужен секрет — модуль зовёт `NotificationService.send`
  напрямую, а в событие кладёт только факт для аудита);
- обработчик обязан терпеть неизвестные поля (forward-compatible); удаление/
  переименование поля = `version + 1`;
- подписки регистрируются на старте; `listens_events` в `feature.toml` обязан
  совпадать с фактическими подписками (тест CI, Фаза 6).

Известное ограничение v1: publish после commit → падение процесса между commit
и постановкой arq-джобы теряет событие. Внутримодульные критичные реакции
поэтому не ходят через шину (см. активацию подписки, 3.3). Transactional
outbox — см. Открытые вопросы (№2).

---

## 3. Публичные интерфейсы модулей ядра

Все методы `async`, кроме регистрационных, зовущихся на старте до первого
запроса, — они синхронны: `AccessService.register_permissions`,
`NotificationService.register_templates`, `AdminRegistry.register/screens`.
Все ошибки — из иерархии 2.4. DTO — Pydantic-модели (frozen), не ORM-объекты:
ORM никогда не пересекает границу модуля.

**Правило сигнатур (tenant-скоупинг).** Методы, работающие с данными текущего
тенанта, **не принимают `tenant_id`** — он берётся из `ctx` и совпадает с
фильтром Repository (2.2): двух источников правды нет. Явный параметр
`tenant_id` остаётся только у методов, легитимно вызываемых вне
тенант-контекста (system/платформенный админ, `ctx.tenant_id is None`) — это
отмечено в docstring. Передача `tenant_id != ctx.tenant_id` при непустом
`ctx.tenant_id` — `PermissionDeniedError`.

**Ключевые кросс-модульные DTO зафиксированы эскизами состава** в своих
разделах: `UserDTO` (3.1), `PaymentDTO` и `CheckoutDTO` (3.3),
`RequestContext` (2.1). Их поля — минимальный контракт: удалить/переименовать
поле — breaking change, добавить — нет. Остальные DTO (`TokenPair`,
`TenantDTO`, `MembershipDTO`, …) фиксируются в фазах реализации.

### 3.1. core/auth

Пользователи — глобальная таблица (не tenant-скоуп): один аккаунт — много
тенантов. Self-service методы работают в user-контексте — субъект берётся
из `ctx.actor`, а не из параметра.

```python
@dataclass(frozen=True, slots=True)   # эскиз состава; в коде — Pydantic frozen
class UserDTO:
    id: UUID
    email: str
    full_name: str | None
    phone: str | None                 # E.164; источник адресов для UserRecipient (3.4)
    locale: str
    two_factor_enabled: bool

class AuthService:
    async def register(self, email: str, password: str, locale: str = "ru") -> UserDTO:
        """Создаёт пользователя (argon2id); ConflictError при занятом email."""
    async def authenticate(self, email: str, password: str, *, ip: str, user_agent: str) -> LoginResult:
        """Проверяет пару; возвращает TokenPair либо TwoFactorChallenge (если включён TOTP).
        AuthenticationError без раскрытия, что именно неверно; RateLimitedError при переборе."""
    async def complete_two_factor(self, challenge_token: str, totp_code: str) -> TokenPair:
        """Обменивает короткоживущий challenge + TOTP-код на TokenPair."""
    async def refresh(self, refresh_token: str) -> TokenPair:
        """Ротация refresh; повторное использование отозванного токена гасит всю семью."""
    async def logout(self, refresh_token: str) -> None:
        """Отзывает refresh-токен текущего пользователя."""
    async def change_password(self, current_password: str, new_password: str) -> None:
        """Смена пароля текущего пользователя (ctx.actor); отзывает все его refresh-токены."""
    async def request_password_reset(self, email: str) -> None:
        """Всегда молча успешна (нет user enumeration); письмо шлёт напрямую через NotificationService."""
    async def reset_password(self, reset_token: str, new_password: str) -> None:
        """Сброс по одноразовому токену; отзывает все refresh-токены."""
    async def enable_totp(self) -> TotpSetup:
        """Генерирует секрет для текущего пользователя; возвращает otpauth-URI для QR.
        Активируется только confirm_totp."""
    async def confirm_totp(self, totp_code: str) -> None:
        """Подтверждает и включает 2FA текущему пользователю."""
    async def disable_totp(self, totp_code: str) -> None:
        """Выключает 2FA (требует действующий код)."""
    async def get_user(self, user_id: UUID) -> UserDTO:
        """Профиль пользователя; NotFoundError. Межмодульный lookup (users — глобальная
        таблица); /me — этот же метод с ctx.actor.id."""

@dataclass(frozen=True, slots=True)
class PermissionDef:
    code: str                # "tenants.member:invite"
    title_key: str           # ключ i18n для admin-UI

class AccessService:
    """Читающая сторона RBAC. Владелец связки user × tenant × role — tenants
    (membership, 3.2); AccessService читает её через публичный
    TenantService.get_membership и никогда не пишет."""

    async def has_permission(self, user_id: UUID, permission: str) -> bool:
        """Проверка права в текущем тенанте (ctx): роль ← membership."""
    async def require(self, user_id: UUID, permission: str) -> None:
        """То же, но PermissionDeniedError — для проверки на уровне СЕРВИСА
        (вторая линия после роутера)."""
    async def list_permissions(self, user_id: UUID) -> frozenset[str]:
        """Все права пользователя в текущем тенанте (для admin-UI и /me)."""
    def register_permissions(self, module: str, permissions: Sequence[PermissionDef]) -> None:
        """Вызывается модулями на старте: декларация каталога кодов прав.
        Дубль кода (в т.ч. из разных фич) или require_permission
        с незадекларированным кодом — ошибка старта."""
```

Публичные FastAPI-зависимости: `current_user() -> UserDTO`,
`require_permission(code)`, `public_endpoint(reason)`,
`authenticated_endpoint(reason)` (раздел 5).

**Не публично:** таблицы users/refresh_tokens/roles, хеширование, подпись и
парсинг JWT, хранение TOTP-секретов, rate limiter, challenge-токены.

### 3.2. core/tenants

Владелец membership и роли в тенанте — tenants: назначение/снятие роли —
только здесь (auth только читает, 3.1). Циклической зависимости auth↔tenants
нет: tenants не зовёт auth для ролей.

```python
class TenantService:
    # --- user-контекст (аутентифицирован, ctx.tenant_id может быть None) ---
    async def create_tenant(self, name: str) -> TenantDTO:
        """Создаёт организацию; владелец — текущий пользователь (ctx.actor), роль owner."""
    async def list_user_tenants(self) -> Sequence[TenantDTO]:
        """Тенанты текущего пользователя (для выбора контекста при входе)."""
    async def accept_invitation(self, invitation_token: str) -> MembershipDTO:
        """Обменивает токен на membership текущего пользователя; тенант берётся из
        приглашения (внутренняя system-запись); ConflictError если уже участник."""

    # --- тенант-контекст (tenant_id из ctx) ---
    async def get_tenant(self) -> TenantDTO:
        """Карточка текущего тенанта."""
    async def update_tenant(self, *, name: str | None = None,
                            default_locale: str | None = None) -> TenantDTO:
        """Частичное обновление настроек текущего тенанта."""
    async def invite_member(self, email: str, role: str) -> InvitationDTO:
        """Создаёт приглашение с TTL (invited_by — ctx.actor); письмо с токеном
        шлёт напрямую через NotificationService."""
    async def revoke_invitation(self, invitation_id: UUID) -> None:
        """Аннулирует непринятое приглашение."""
    async def list_members(self, page: Page) -> PageResult[MembershipDTO]:
        """Участники текущего тенанта."""
    async def get_membership(self, user_id: UUID) -> MembershipDTO | None:
        """Membership пользователя в текущем тенанте или None — используется
        auth.AccessService при проверке прав."""
    async def change_member_role(self, user_id: UUID, role: str) -> MembershipDTO:
        """Меняет роль участника; InvariantViolationError при попытке разжаловать
        последнего owner."""
    async def remove_member(self, user_id: UUID) -> None:
        """Удаляет участника; InvariantViolationError при попытке удалить последнего owner."""

    # --- платформенный контекст (ctx.tenant_id is None; см. Открытый вопрос №7) ---
    async def set_status(self, tenant_id: UUID, status: TenantStatus, reason: str | None = None) -> None:
        """active | suspended; suspended блокирует API тенанта (middleware).
        Явный tenant_id: вызывается платформенным админом вне тенант-контекста."""
```

Публичная зависимость: `current_tenant() -> TenantContext`.

**Не публично:** таблицы tenants/memberships/invitations, генерация и
хранение invitation-токенов.

### 3.3. core/billing

Два публичных сервиса: подписки (тарифы шаблона) и платежи (универсальный
приём денег — им пользуется и сам billing, и commerce в Фазе 6).

```python
class BillingService:
    async def list_plans(self) -> Sequence[PlanDTO]:
        """Активные тарифы (цена — Money); глобальный справочник."""
    async def get_subscription(self) -> SubscriptionDTO | None:
        """Текущая подписка тенанта (ctx) или None."""
    async def start_subscription(self, plan_code: str, provider: str) -> CheckoutDTO:
        """Создаёт pending-подписку + платёж; возвращает checkout (URL оплаты).
        Активация подписки — В ТОЙ ЖЕ ТРАНЗАКЦИИ, где billing финализирует
        платёж с purpose="subscription" (внутренний вызов, не через шину:
        «деньги списаны, подписка не активна» исключено конструктивно).
        Событие billing.subscription.activated публикуется для внешних
        подписчиков как свершившийся факт."""
    async def cancel_subscription(self) -> SubscriptionDTO:
        """Отменяет автопродление; доступ — до конца оплаченного периода."""

@dataclass(frozen=True, slots=True)
class PaymentProviderInfo:
    code: str            # "payme" | "click"
    title_key: str       # ключ i18n для UI выбора способа оплаты
    enabled: bool

@dataclass(frozen=True, slots=True)   # эскиз состава
class PaymentDTO:
    id: UUID
    status: Literal["created", "pending", "succeeded", "failed", "canceled", "expired"]
    amount: Money
    purpose: str
    reference: str
    provider: str
    paid_at: datetime | None

@dataclass(frozen=True, slots=True)   # эскиз состава
class CheckoutDTO:
    payment_id: UUID              # модуль-заказчик сохраняет связь object→payment по нему
    provider: str
    checkout_url: str
    expires_at: datetime | None   # TTL checkout; по истечении платёж уйдёт в expired

class PaymentService:
    async def list_providers(self) -> Sequence[PaymentProviderInfo]:
        """Провайдеры, включённые конфигом, — для выбора способа оплаты в UI.
        Модуль-заказчик не хардкодит коды провайдеров."""
    async def create_payment(
        self, amount: Money, *,
        purpose: str,             # "subscription" | "commerce.order" | ... (namespace модуля-заказчика)
        reference: str,           # id объекта в модуле-заказчике (order_id и т.п.)
        provider: str,            # код из list_providers()
        idempotency_key: str,     # уникален в тенанте; повтор -> тот же Payment, без дубля
        return_url: str | None = None,
    ) -> CheckoutDTO:
        """Создаёт платёж (tenant — из ctx) и checkout у провайдера. Итог платежа
        модуль-заказчик узнаёт из событий billing.payment.succeeded|failed|
        canceled|expired по (purpose, reference) — НЕ колбэком."""
    async def get_payment(self, payment_id: UUID) -> PaymentDTO:
        """Состояние платежа: created|pending|succeeded|failed|canceled|expired."""
    async def cancel_payment(self, payment_id: UUID) -> PaymentDTO:
        """Отмена неоплаченного платежа; ConflictError если уже финализирован."""
```

Ключевой контракт для commerce (Фаза 6): commerce **не знает** про Payme/Click,
он показывает выбор оплаты из `list_providers()`, зовёт
`PaymentService.create_payment(purpose="commerce.order", reference=str(order_id))`
и подписывается на `billing.payment.succeeded|failed|canceled|expired`
(включая `expired` — иначе брошенный checkout навсегда завесит заказ и резерв).
Протухание платежей — платформенная arq-джоба billing по TTL из конфига.

**Не публично:** адаптеры Payme/Click, роутеры вебхуков, таблицы
payments/payment_webhooks/subscriptions/plans, машина состояний платежа,
merchant-креды, джоба протухания.

### 3.4. core/notifications

```python
type Recipient = UserRecipient | AddressRecipient

@dataclass(frozen=True, slots=True)
class UserRecipient:
    user_id: UUID                      # адреса возьмутся из профиля (UserDTO: email, phone)

@dataclass(frozen=True, slots=True)
class AddressRecipient:
    channel: str                       # "telegram" | "sms_eskiz" | "email"
    address: str                       # chat_id | телефон E.164 | email

@dataclass(frozen=True, slots=True)
class TemplateDef:
    key: str                           # "<module>.<purpose>", напр. "commerce.order_paid"
    default_channels: Sequence[str]    # каналы, если send не передал channels
    required_context: frozenset[str]   # переменные, обязательные в context

class NotificationService:
    def register_templates(self, module: str, templates: Sequence[TemplateDef]) -> None:
        """Вызывается модулем на старте (симметрично register_permissions и
        AdminRegistry.register). Файлы шаблонов модуль поставляет в своей папке
        templates/<locale>/ (ru и uz обязательны). Дубль ключа или отсутствие
        файла для обязательной локали — ошибка старта. Механика рендеринга —
        Фаза 3; контракт регистрации фиксируется здесь, чтобы commerce
        подключал свои шаблоны, не трогая ядро."""
    async def send(
        self, recipient: Recipient,
        template: str,                          # ключ из зарегистрированного каталога
        context: Mapping[str, object],
        *,
        channels: Sequence[str] | None = None,  # None -> default_channels шаблона
        locale: str | None = None,              # None -> цепочка: получатель -> тенант -> "ru"
        dedup_key: str | None = None,           # идемпотентность: повтор с тем же ключом не шлёт дубль
    ) -> UUID:
        """Ставит отправку в очередь arq и сразу возвращает notification_id
        (tenant — из ctx; None для платформенных писем: сброс пароля).
        NotFoundError на неизвестный шаблон; InvariantViolationError, если
        у получателя нет адреса ни для одного канала или в context нет
        required_context шаблона."""
    async def get_status(self, notification_id: UUID) -> NotificationStatusDTO:
        """queued | sent | partially_failed | failed — по каналам."""
    async def set_channel_config(self, channel: str, config: Mapping[str, object]) -> None:
        """Записывает per-tenant конфиг канала (tenant — из ctx): токен бота,
        ключи Eskiz, SMTP-креды (таблица notification_settings — схема §2.4).
        Write-only-контракт (threat model, V10): config валидируется по схеме
        канала при записи, шифруется и обратно не читается — метода, возвращающего
        секреты, не существует. NotFoundError — незарегистрированный канал;
        InvariantViolationError — config не проходит схему канала."""
    async def get_channel_status(self, channel: str) -> ChannelStatusDTO:
        """Состояние канала для admin-UI текущего тенанта: настроен ли и включён ли
        (признак настроенности/маска; секрет в открытом виде не возвращается —
        write-only-контракт выше)."""
```

**Не публично:** адаптеры каналов, рендеринг шаблонов, arq-джобы, таблицы
notification_settings/notification_outbox; значения секретов каналов (токены
ботов, API-ключи) — запись только через `set_channel_config`, чтения не
существует.

### 3.5. core/audit

```python
class AuditService:
    async def record(
        self, *,
        action: str,                       # та же конвенция, что имена событий: "auth.user.password_changed"
        object_type: str | None = None,    # "payment", "member"
        object_id: str | None = None,
        metadata: Mapping[str, object] | None = None,   # без секретов и полных PII
        event_id: UUID | None = None,      # связка с событием шины — механизм дедупликации, см. ниже
        request: RequestContext | None = None,          # shared/context.py (2.1): ip, user_agent
    ) -> None:
        """Пишет запись в ту же транзакцию, что и бизнес-действие; tenant_id и
        actor — из ctx. Критичные действия зовут record напрямую из сервиса."""
    async def search(self, query: AuditQuery, page: Page) -> PageResult[AuditRecordDTO]:
        """Поиск для admin-экрана текущего тенанта: по action, actor, объекту,
        диапазону дат."""
```

Как audit получает события, включая события будущих модулей:

- audit — **wildcard-подписчик**: `bus.subscribe("*", reliable=True)`.
  Подключение commerce (и любого будущего модуля) не требует правок
  core/audit — ядро не знает имён чужих событий и не должно;
- exclusion-список высокочастотной телеметрии, которую не дублируем в audit, —
  константа в core/audit; v1: `["notifications.message.sent"]`;
- **одно действие — одна запись** (без двойной записи): критичное действие
  в сервисе делает `event_id = self.emit(...)` и `audit.record(...,
  event_id=event_id)` в той же транзакции; wildcard-подписчик пишет запись
  идемпотентно по `event_id` (колонка + уникальный partial-индекс,
  `ON CONFLICT DO NOTHING` — дополнение к документу схемы БД). Прямая запись
  уже закоммичена к моменту доставки — дубля нет ни при каком порядке;
- критичные действия v1 (прямой `record` в транзакции + событие):
  `auth.user.password_changed`, `auth.user.two_factor_enabled|disabled`,
  `tenants.tenant.status_changed`, `tenants.member.removed`, все финализации
  `billing.payment.*`. Остальные действия попадают в audit только через
  wildcard-подписку.

Гарантия append-only — сама форма интерфейса: методов update/delete **не
существует** (+ запрет UPDATE/DELETE на уровне БД — в документе схемы).

**Не публично:** таблица audit_log, wildcard-подписчик, exclusion-список.

### 3.6. core/admin

Admin — каркас: авторизация, права и реестр экранов. Своей бизнес-логики нет.

```python
@dataclass(frozen=True, slots=True)
class AdminScreen:
    slug: str              # уникален; сегмент URL: /api/admin/{slug}
    title_key: str         # ключ i18n-каталога
    module: str            # владелец экрана: имя core-модуля ("billing") или фичи
                           # из feature.toml ("commerce.orders") — для диагностики
    router: APIRouter      # эндпоинты экрана; каждый обязан иметь require_permission
    permission: str        # право на видимость раздела, напр. "billing.subscription:read"

class AdminRegistry:
    def register(self, screen: AdminScreen) -> None:
        """Вызывается модулем/фичей на старте. Дубль slug -> ошибка старта."""
    def screens(self) -> Sequence[AdminScreen]:
        """Все зарегистрированные экраны (для валидации на старте)."""
    async def screens_for(self, user_id: UUID) -> Sequence[AdminScreenInfo]:
        """Экраны, на которые у пользователя есть право в текущем тенанте (ctx), —
        меню админки."""
```

**Не публично:** монтирование роутеров, middleware админки.

---

## 4. Порты внешних систем

Порты — Protocol-интерфейсы в своих модулях (`core/billing/ports.py`,
`core/notifications/ports.py`). Реализации выбираются конфигом, каждая в своём
файле. Общие требования ко всем реализациям (контракт, проверяется тестами):

- **Таймауты:** connect 5 s, полный запрос 15 s (конфигурируемо). Без таймаута
  внешних вызовов не бывает.
- **Ретраи:** только для идемпотентных исходящих операций; максимум 3 попытки,
  экспоненциальный backoff с джиттером. Неидемпотентное — не ретраится без
  идемпотентного ключа.
- **Circuit breaker** на каждый провайдер/канал: 5 подряд ошибок → open на
  60 s → half-open проба. Открыт → `CircuitOpenError` мгновенно; система
  деградирует (платёж отклоняется с понятной ошибкой, уведомление ждёт в
  очереди), но не падает.
- Ошибки провайдера → `PaymentProviderError` / `NotificationChannelError`;
  сырые ответы — в логи (без секретов), наружу — нормализованная ошибка.

### 4.1. PaymentProvider (реализации: Payme, Click)

Специфика УЗ-провайдеров: основной поток — **они вызывают нас** (merchant
callbacks), а не мы их. Поэтому порт симметричен: исходящий `create_checkout`
+ нормализация входящих колбэков.

```python
# core/billing/ports.py
class PaymentProvider(Protocol):
    code: ClassVar[str]                       # "payme" | "click"

    async def create_checkout(self, payment: PaymentDTO, return_url: str | None) -> CheckoutDTO:
        """Готовит оплату: URL/параметры для редиректа плательщика."""

    async def parse_webhook(self, raw: RawWebhook) -> ProviderCallback:
        """Верифицирует и нормализует колбэк провайдера.
        Payme: Basic-auth заголовок с merchant key (сравнение constant-time),
               методы JSON-RPC CheckPerformTransaction/CreateTransaction/
               PerformTransaction/CancelTransaction/CheckTransaction.
        Click: prepare/complete, подпись md5(sign_string).
        Неверная подпись/авторизация -> WebhookVerificationError (наружу уйдёт
        в диалекте провайдера, см. «обработка исходов» ниже)."""

    def build_webhook_response(self, outcome: CallbackOutcome) -> WebhookResponse:
        """Формирует ответ в диалекте провайдера для ЛЮБОГО исхода — успешного
        и ошибочного (JSON-RPC result/error у Payme, error-коды Click),
        включая корректный ответ на ПОВТОРНУЮ доставку."""

@dataclass(frozen=True, slots=True)
class ProviderCallback:
    provider: str
    provider_txn_id: str | None     # None только для action="check": у Payme
                                    # CheckPerformTransaction транзакции ещё нет
    action: Literal["check", "create", "confirm", "cancel", "status"]
    payment_reference: str          # наш payment_id / merchant order id
    amount: Money
    raw: Mapping[str, Any]

@dataclass(frozen=True, slots=True)
class CallbackOutcome:
    status: Literal[
        "ok",                # переход выполнен
        "already_processed", # повторная доставка — тот же ответ, детерминированно из состояния платежа (схема §2.3)
        "invalid_signature", # подпись/авторизация не сошлась
        "not_found",         # платёж по reference не найден
        "amount_mismatch",   # сумма колбэка != сумме платежа
        "invalid_state",     # недопустимый переход машины состояний
    ]
    payment: PaymentDTO | None
    detail: str | None
```

Обработка исходов — вне общего error-хендлера:

- роуты вебхуков **исключены из общего маппинга DomainError→HTTP** (2.4):
  Payme ожидает JSON-RPC error (например, −32504) с HTTP 200, Click — свои
  error-коды в теле. Общий 403 сломал бы ретраи и сверку провайдера;
- **любой** исход — невалидная подпись, неизвестный платёж, недопустимый
  переход, несовпадение суммы, повторная доставка — отвечается через
  `build_webhook_response(outcome)` в диалекте провайдера;
- `WebhookVerificationError.http_status = 403` — только fallback для запросов,
  в которых не удалось распознать даже формат провайдера (диалект ответа
  неопределим).

Контракты:

- **Финализация платежа выполняется в tenant-контексте платежа** — повышение
  из system-контекста после идентификации через `SystemRepository` (механизм —
  2.1); system-контекст бизнес-данные не пишет.
- **Идемпотентность.** Уникальный constraint `(provider, provider_txn_id)` для
  колбэков, несущих транзакцию (`create|confirm|cancel|status`); повторный
  колбэк не создаёт транзакцию и не двигает состояние, а возвращает тот же
  результат (`already_processed`), детерминированно построенный из состояния
  платежа (схема §2.3 — тело ответа не хранится). `action="check"` — read-only
  проверка возможности платежа: состояния не меняет и в дедупликации не
  участвует (идемпотентен по природе). `create_payment` идемпотентен по
  `(tenant_id, idempotency_key)`.
- **Машина состояний платежа** — в billing (не в адаптере): переходы только
  вперёд (`created → pending → succeeded | failed | canceled | expired`);
  колбэк, требующий недопустимого перехода, получает `invalid_state` в
  диалекте провайдера, состояние не меняется.
- **Возвраты (refund) в v1 не поддерживаются — решение явное, не умолчание.**
  `succeeded` терминален, `Money` запрещает отрицательные суммы. Payme
  `CancelTransaction` по уже выполненной транзакции (сценарий сторно,
  предусмотренный протоколом провайдера) получает отказ в диалекте провайдера
  (−31007 «заказ выполнен, отмена невозможна»), Click — соответствующий
  error-код; попытка фиксируется в audit. Возврат денег покупателю в v1 —
  ручная операция вне системы. Следствие для commerce: события «оплаченный
  заказ отменён провайдером» в v1 не существует. См. Открытый вопрос №11.
- **Сверка суммы**: `callback.amount != payment.amount` → `amount_mismatch`
  в диалекте провайдера + запись в audit.
- Неверная подпись → `invalid_signature` в диалекте провайдера, ноль изменений
  состояния, событие в audit (негативный тест обязателен).
- Эндпоинты вебхуков помечаются `public_endpoint(reason="provider signature auth")`
  (раздел 5) — их аутентификация не JWT, а подпись.

### 4.2. NotificationChannel (реализации: Telegram, Eskiz SMS, email/SMTP)

```python
# core/notifications/ports.py
class NotificationChannel(Protocol):
    code: ClassVar[str]                       # "telegram" | "sms_eskiz" | "email"

    async def send(self, message: RenderedMessage) -> ChannelResult:
        """Отправляет ОДНО отрендеренное сообщение одному адресату.
        ChannelTemporaryError -> ретрай arq; ChannelPermanentError
        (кривой адрес, недоставляемо) -> без ретраев, фиксация отказа."""

@dataclass(frozen=True, slots=True)
class RenderedMessage:
    notification_id: UUID
    address: str                # chat_id | E.164 | email
    subject: str | None         # только email
    body: str
    locale: str

@dataclass(frozen=True, slots=True)
class ChannelResult:
    provider_message_id: str | None
```

Контракты:

- Вызов только из arq-джобы; джоба идемпотентна: перед отправкой проверяет
  delivery-запись `(notification_id, channel)` — повторная доставка джобы не
  дублирует SMS.
- Ретраи через arq: 5 попыток, backoff 1→2→4→8→16 мин, затем dead letter +
  событие `notifications.message.failed`.
- Eskiz: bearer-токен с автопродлением (внутренность адаптера); Telegram: Bot
  API; email: SMTP из конфига.
- Секреты каналов — только из окружения; в логах маскируются адреса
  (телефон → `+9989•••••67`).

---

## 5. Механика require_permission и регистрация admin-экранов

### 5.1. Формат кода права — конвенция (зафиксирована)

```
<module>.<resource>:<action>
```

- `module` — имя core-модуля (`tenants`, `billing`) или бизнес-модуля
  (`commerce`) — **без имени фичи**, симметрично конвенции имён событий (6.1):
  право объявляет фича `commerce.orders`, но код права — `commerce.order:read`.
  Коллизии ресурсов между фичами одного модуля исключает
  `register_permissions`: дубль кода — ошибка старта (3.1);
- `resource` — существительное в ед. числе, snake_case;
- `action` — из базового набора `read | create | update | delete` либо
  зарегистрированный глагол (`cancel`, `invite`, `export`).

Примеры: `tenants.member:invite`, `billing.payment:create`,
`billing.subscription:cancel`, `audit.record:read`, `commerce.order:update`.

Каталог прав декларируется модулем на старте через
`AccessService.register_permissions(...)`; незадекларированный код в
`require_permission` — ошибка старта.

### 5.2. Декларация на эндпоинте — три маркера

Каждый роут несёт ровно один из трёх маркеров:

| Маркер | Кто проходит | Примеры |
|---|---|---|
| `require_permission(code)` | член тенанта с правом (RBAC) | всё тенант-скоупное: члены, платежи, админка |
| `authenticated_endpoint(reason)` | любой аутентифицированный пользователь, тенант-контекст не требуется; авторизация объекта — в сервисе (субъект = ctx.actor) | `/me`, change_password, enable/confirm/disable_totp, logout, create_tenant, list_user_tenants, accept_invitation |
| `public_endpoint(reason)` | без JWT; аутентификация иная или не нужна | login, register, refresh, complete_two_factor (challenge-токен), request/reset password (одноразовый токен), вебхуки платёжек (подпись) |

```python
@router.post(
    "/invitations",
    dependencies=[require_permission("tenants.member:invite")],
)
async def invite_member(...) -> InvitationDTO: ...

@router.post(
    "/tenants",
    dependencies=[authenticated_endpoint(reason="user-scoped: создание своей организации")],
)
async def create_tenant(...) -> TenantDTO: ...

@router.post("/webhooks/payme", dependencies=[public_endpoint(reason="Payme Basic-auth + JSON-RPC")])
async def payme_webhook(...) -> WebhookResponse: ...
```

`require_permission(code)` — **фабрика**: возвращает объект зависимости,
несущий машиночитаемый маркер (`__permission__ = code` на callable) **с момента
декларации**, а не с первого запроса — стартовая валидация (5.3) видит его до
приёма трафика. В рантайме запроса зависимость:
1. извлекает `current_user` + `current_tenant`;
2. зовёт `AccessService.require(user_id, code)` (тенант — из ctx) → 403 при
   отказе.

`authenticated_endpoint(reason)` проверяет JWT, его маркер —
`__authenticated__`; `public_endpoint(reason)` ничего не проверяет, маркер —
`__public__`. `reason` обязателен у обоих — это осознанное решение, видимое
в ревью и в валидации.

Права дублируются **на уровне сервиса** (`AccessService.require` внутри
метода; для authenticated-маршрутов — проверка принадлежности объекта
ctx.actor) — роутер не единственная линия.

### 5.3. Валидация на старте

На событии startup приложение обходит `app.routes`; маркеры ищутся
**интроспекцией зависимостей роута** (`route.dependencies` / dependant-дерево) —
до первого запроса:

- инфраструктурные пути (`/health`, `/ready`, `/metrics`, `/docs`, `/openapi.json`)
  — в белом списке;
- каждый остальной роут обязан нести **ровно один** маркер из трёх:
  `__permission__`, `__authenticated__` или `__public__`;
- каждый `__permission__` обязан существовать в каталоге прав.

Нарушение → `RuntimeError` со списком проблемных роутов, приложение **не
стартует**. Тот же обход — тестом в CI (падает раньше деплоя).

### 5.4. Регистрация admin-экранов

Контракт (реализация — Фаза 4): модуль в своём `admin.py` объявляет
`AdminScreen` и вызывает `admin_registry.register(screen)` при загрузке модуля.
На старте admin:

1. проверяет уникальность slug и существование `screen.permission` в каталоге;
2. монтирует `screen.router` под `/api/admin/{slug}`;
3. прогоняет по смонтированным роутам ту же валидацию 5.3 (у каждого
   admin-эндпоинта — своё право; `authenticated`/`public` в админке запрещены).

`GET /api/admin/screens` (право `admin.screen:read`) возвращает меню — только
экраны, доступные пользователю (`screens_for`). Выключенный модуль не
импортируется → его экранов физически нет.

---

## 6. События шины

### 6.1. Конвенция имён — зафиксирована

```
<module>.<entity>.<action>
```

- `module` — издатель (`auth`, `billing`, `commerce`); для фич — без имени фичи
  (`commerce.order.created`, а не `commerce.orders.order.created`);
- `entity` — сущность в ед. числе;
- `action` — глагол в прошедшем времени / причастие (`created`, `succeeded`,
  `checked_out`): событие — свершившийся факт.

Обоснование: префикс модуля исключает коллизии между будущими модулями
(crm и commerce оба захотят `order.*`/`deal.*`), даёт сортировку и грепаемость,
1:1 ложится на `publishes_events` в feature.toml и на `action` в audit.
Расхождение с короткими именами из мастер-промпта (`order.created`) — Открытый
вопрос №1.

### 6.2. Конверт

Единый `EventEnvelope` (см. 2.6): `event_id`, `name`, `version`, `occurred_at`,
`tenant_id`, `actor`, `payload`. Ниже в таблицах — только `payload`; типы
указаны семантически, на проводе `UUID` и `datetime` — строки (2.6).

### 6.3. События ядра v1

audit в таблице не повторяется: он — wildcard-подписчик всех событий
(reliable, arq; см. 3.5), кроме exclusion-списка (v1:
`notifications.message.sent`). Дедупликация с прямыми записями критичных
действий — по `event_id` (3.5). Колонки «Подписчики» и «Доставка» — про
остальных подписчиков.

| Событие | Payload (поле: тип) | Издатель | Подписчики v1 (кроме audit) | Доставка |
|---|---|---|---|---|
| `auth.user.registered` | `user_id: UUID`, `email: str`, `locale: str` | auth | — | — |
| `auth.user.login_succeeded` | `user_id: UUID`, `ip: str`, `user_agent: str` | auth | — | — |
| `auth.user.login_failed` | `email: str`, `ip: str`, `reason: str` | auth | — | — |
| `auth.user.password_changed` | `user_id: UUID` | auth | — | — |
| `auth.user.password_reset_requested` | `user_id: UUID` (токен — НЕ в payload) | auth | — | — |
| `auth.user.two_factor_enabled` | `user_id: UUID` | auth | — | — |
| `auth.user.two_factor_disabled` | `user_id: UUID` | auth | — | — |
| `tenants.tenant.created` | `tenant_id: UUID`, `name: str`, `owner_user_id: UUID` | tenants | *billing (авто-подписка) — Открытый вопрос №6* | arq |
| `tenants.tenant.status_changed` | `tenant_id: UUID`, `status: str`, `reason: str \| None` | tenants | — | — |
| `tenants.member.invited` | `tenant_id: UUID`, `invitation_id: UUID`, `email: str`, `role: str` (токен — НЕ в payload) | tenants | — | — |
| `tenants.member.joined` | `tenant_id: UUID`, `user_id: UUID`, `role: str` | tenants | — | — |
| `tenants.member.removed` | `tenant_id: UUID`, `user_id: UUID` | tenants | — | — |
| `tenants.member.role_changed` | `tenant_id: UUID`, `user_id: UUID`, `role: str` | tenants | — | — |
| `billing.payment.created` | `payment_id: UUID`, `amount: int`, `currency: str`, `purpose: str`, `reference: str`, `provider: str` | billing | — | — |
| `billing.payment.succeeded` | как `payment.created` + `paid_at: datetime` | billing | notifications (чек об оплате); *Фаза 6: commerce.orders* | arq |
| `billing.payment.failed` | как `payment.created` + `reason: str` | billing | *Фаза 6: commerce.orders* | arq |
| `billing.payment.canceled` | как `payment.created` | billing | *Фаза 6: commerce.orders* | arq |
| `billing.payment.expired` | как `payment.created` | billing | *Фаза 6: commerce.orders (освобождение резервов)* | arq |
| `billing.subscription.activated` | `subscription_id: UUID`, `plan_code: str`, `current_period_end: datetime` | billing | notifications | arq |
| `billing.subscription.canceled` | `subscription_id: UUID`, `plan_code: str` | billing | — | — |
| `billing.subscription.expired` | `subscription_id: UUID`, `plan_code: str` | billing | notifications | arq |
| `notifications.message.sent` | `notification_id: UUID`, `channel: str`, `template: str` (адрес — НЕ в payload) | notifications | — (метрики in-process в arq-воркере — процессе издателя; в audit не пишется: exclusion) | in-process |
| `notifications.message.failed` | `notification_id: UUID`, `channel: str`, `template: str`, `error: str`, `attempts: int` | notifications | — | — |

Payload финализаций платежа (`succeeded|failed|canceled|expired`) унифицирован
«как `payment.created`» — подписчик и audit получают одинаковый состав полей
на любой исход.

audit и admin собственных событий не публикуют: audit — сток; действия админа
фиксируются событиями доменных модулей и прямыми `AuditService.record`.

Активация подписки по факту оплаты — **не через шину**: billing активирует
подписку в той же транзакции, где финализирует платёж с
`purpose="subscription"` (см. 3.3); `billing.subscription.activated`
публикуется как факт для внешних подписчиков.

### 6.4. Имена событий commerce — зарезервированы для Фазы 6

Только имена и эскиз payload; проектирование commerce — Фаза 6. Покупатель
во всех payload — единообразно `customer_user_id`.

| Событие | Эскиз payload |
|---|---|
| `commerce.product.created` | `product_id: UUID` |
| `commerce.product.updated` | `product_id: UUID` |
| `commerce.product.archived` | `product_id: UUID` |
| `commerce.cart.checked_out` | `cart_id: UUID`, `customer_user_id: UUID`, `total_amount: int`, `currency: str` |
| `commerce.order.created` | `order_id: UUID`, `customer_user_id: UUID`, `total_amount: int`, `currency: str` |
| `commerce.order.paid` | `order_id: UUID`, `payment_id: UUID` |
| `commerce.order.canceled` | `order_id: UUID`, `reason: str` |

### 6.5. Проверка достаточности: сквозной сценарий Фазы 6

Создание заказа → оплата → уведомление → аудит → админка — только через
контракты этого документа, без изменений ядра:

1. `orders` создаёт заказ, публикует `commerce.order.created`, показывает
   выбор оплаты из `PaymentService.list_providers()` и зовёт
   `PaymentService.create_payment(amount, purpose="commerce.order",
   reference=order_id, provider, idempotency_key)` → сохраняет
   `CheckoutDTO.payment_id` у заказа, отдаёт checkout-URL покупателю.
2. Вебхук Payme/Click → `PaymentProvider.parse_webhook` (подпись) → billing
   финализирует платёж в tenant-контексте платежа (повышение из
   system-контекста, 2.1) → `billing.payment.succeeded`. Любой ошибочный
   исход — ответ в диалекте провайдера через `build_webhook_response` (4.1).
3. `orders` (reliable-подписчик; dedup по `event_id` встроен в шину, 2.6)
   помечает заказ оплаченным по `reference` в UoW своей джобы, публикует
   `commerce.order.paid` (после commit этого UoW) и зовёт
   `NotificationService.send(..., template="commerce.order_paid")` — шаблон
   зарегистрирован фичей через `register_templates` (3.4).
   Параллельно `orders` подписан на `billing.payment.failed|canceled|expired` —
   отказные ветки и освобождение резервов.
4. audit получает всю цепочку событий wildcard-подпиской (включая
   `commerce.*` — без правок core/audit) + прямые записи критичных шагов,
   дедуплицированные по `event_id`.
5. `orders/admin.py` регистрирует `AdminScreen(slug="orders",
   permission="commerce.order:read", router=...)` — экран появляется в меню.

Заметка: доступ покупателя к storefront-эндпоинтам (каталог, корзина, заказ)
не покрывается тенантным RBAC — покупатель не член тенанта. Механизм выбран
не до конца — Открытый вопрос №10.

---

## 7. Открытые вопросы

1. **Имена событий: с префиксом модуля или короткие.**
   Мастер-промпт использует `order.created` / `cart.checked_out`; этот документ
   фиксирует `commerce.order.created` (см. 6.1). Варианты: (а) префиксованные —
   нет коллизий между будущими модулями (crm/commerce), грепаемость;
   (б) короткие — как в мастер-промпте, лаконичнее. **Рекомендация: (а)**;
   тогда примеры в мастер-промпте и feature.toml считаем устаревшей записью.
   От выбора зависит содержимое `publishes_events`/`listens_events` во всех
   манифестах. **Решение (2026-07-06): (а) префиксованные имена (ОВ-09);
   примеры в мастер-промпте исправлены.**

2. **Надёжность публикации: post-commit vs transactional outbox.**
   v1 — публикация после commit (просто, без новых таблиц), окно потери —
   краш процесса между commit и постановкой arq-джобы. Внутримодульные
   критичные реакции через шину не ходят (активация подписки — в транзакции,
   3.3), поэтому окно касается только межмодульных реакций и audit-стока.
   Альтернатива — outbox (таблица событий в той же транзакции + relay), даёт
   exactly-enqueue ценой сложности. **Рекомендация:** v1 — post-commit +
   идемпотентные подписчики; outbox — в бэклог, конверт события уже совместим
   (миграции не потребует).

3. **Merchant-креды платёжек: на деплой или на тенанта.**
   (а) Один набор кредов Payme/Click на клиентский проект (из окружения) —
   типовой случай «шаблон = один бизнес»; (б) креды в таблице per-tenant —
   для маркетплейс-сценария. **Рекомендация: (а) для v1**; per-tenant — в
   бэклог. Выбор влияет на схему БД billing и конфиг адаптеров.
   **Решение (2026-07-06): (а) один набор кредов на деплой, env (ОВ-05).**

4. **Сброс пароля — в scope Фазы 2?** В мастер-промпте не назван
   (регистрация, вход, refresh, 2FA, RBAC), но без него шаблон в прод не
   выйдет; интерфейс уже включён в 3.1. **Рекомендация: включить в Фазу 2.**
   Если нет — убрать 2 метода и событие `auth.user.password_reset_requested`.

5. **Recovery-коды для 2FA.** В 3.1 только TOTP; потеря устройства = потеря
   аккаунта (решается вручную через админа). **Рекомендация: добавить
   одноразовые recovery-коды в Фазу 2** (малая цена, стандарт безопасности;
   схема БД уже содержит `user_recovery_codes`); иначе задокументировать
   процедуру ручного сброса 2FA админом.

6. **Автоподписка при создании тенанта.** Создавать ли trial/free-подписку
   в ответ на `tenants.tenant.created` (подписчик billing)? Варианты: (а) без
   подписки — биллинг подключается явно; (б) авто-free/trial из конфига.
   **Рекомендация: (б) с планом по умолчанию из конфига** — типовой SaaS-флоу,
   реализуется одним подписчиком. Влияет на события billing и seed тарифов.

7. **Платформенный суперадмин vs админ тенанта.** Admin-каркас обслуживает
   оба уровня одним механизмом прав, но нужен носитель платформенной роли
   (управление тенантами — `TenantService.set_status`, просмотр всех
   платежей). Варианты: (а) флаг/роль `platform_admin` вне тенантов;
   (б) служебный «платформенный тенант». **Рекомендация: (а)** — проще в RLS
   и проверке прав. Влияет на схему БД auth и на допустимость
   `ctx.tenant_id is None` (2.1). **Решение (2026-07-06): (а) — флаг/роль
   platform_admin вне тенантов (ОВ-04).**

8. **Result vs типизированные исключения — расхождение с мастер-промптом.**
   Мастер-промпт перечисляет `Result` в составе shared/; документ фиксирует
   иерархию исключений `DomainError` (2.4, обоснование там же). Варианты:
   (а) исключения — один маппинг на HTTP/i18n, нет `unwrap`-шума;
   (б) Result — явные ошибки в сигнатурах ценой шума без checked-типов.
   **Рекомендация: (а)**; тогда упоминание Result в мастер-промпте считаем
   устаревшей записью. Требует утверждения владельцем — это отступление от
   источника правды. **Решение (2026-07-06): (а) типизированные исключения
   DomainError (ОВ-07); мастер-промпт исправлен, зафиксировано в ADR.**

9. **Как тенант попадает в `TenantContext`.** Варианты: (а) tenant-клейм в
   JWT — access-токен выдаётся на пару user×tenant, выбор/смена тенанта =
   обмен через refresh; контекст подписан, `require_permission` не ходит в БД
   за membership на каждый запрос; (б) заголовок `X-Tenant-Id` + проверка
   membership на каждом запросе — один токен на все тенанты, но контекст
   подделываем заголовком и проверка дороже. **Рекомендация: (а)** — влияет
   на дизайн JWT и refresh (Фаза 2); `list_user_tenants` обслуживает экран
   выбора тенанта до выдачи tenant-токена. **Решение (2026-07-06): (а)
   tenant-клейм в JWT (ОВ-03).**

10. **Модель доступа покупателя (storefront, Фаза 6).** Покупатель
    (`customer_user_id`) — аутентифицированный пользователь, но не член
    тенанта: тенантный RBAC (`require_permission`) для него неприменим, а
    `public_endpoint` — ложь (корзина и заказы требуют входа). Варианты:
    (а) авто-роль `customer` в тенанте при первом заказе — единый RBAC, но
    membership раздувается B2C-объёмами; (б) `authenticated_endpoint` +
    ownership-проверка в сервисе (объект принадлежит `ctx.actor`) — маркер
    уже введён (5.2); (в) отдельная ветка customer-прав вне membership.
    **Рекомендация: (б)** — не раздувает membership, вторая линия остаётся
    в сервисе; детальный дизайн — Фаза 6, но решение нужно до схемы commerce.

11. **Возвраты (refund) платежей.** Протокол Payme предусматривает
    `CancelTransaction` в том числе после `PerformTransaction` (сторно
    оплаченной транзакции); v1 отвечает отказом «заказ выполнен» (4.1) —
    возвратов не существует, возврат покупателю — ручной процесс вне системы.
    Варианты: (а) v1 без возвратов, как зафиксировано, — минимальная машина
    состояний, `Money` без отрицательных сумм; (б) полноценный refund в v1 —
    терминальный переход `succeeded → refunded`, событие
    `billing.payment.refunded`, сторно-записи в схеме payments.
    **Рекомендация: (а)**; refund — в бэклог. Расширение обратно совместимо:
    новый переход и новое событие не ломают подписчиков v1. Следствие для
    commerce: сценарий «провайдер отменил оплаченный заказ» в v1 не
    проектируется.
