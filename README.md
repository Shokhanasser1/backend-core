# backend-core

Переиспользуемый шаблон-конструктор бэкенда для клиентских проектов рынка
Узбекистана/СНГ: e-commerce, SaaS, CRM, боты. Архитектура — три уровня:
**ядро → модули → фичи** (ADR-0004). Это не приложение, а starter kit, из
которого клонированием собираются клиентские проекты (ADR-0009).

**Статус:** Фаза 1 (скелет и инфраструктура). Ядро (auth, tenants, billing,
notifications, audit, admin) строится в Фазах 2–4, модуль commerce — в
Фазе 6. План и статусы — `PLAN.md`; требования — `master-prompt-backend-core.md`.

## Стек

Python 3.12+ · FastAPI · SQLAlchemy 2 (async) · Alembic (мультиветочный) ·
PostgreSQL 16 · Redis + arq · structlog · Sentry · Prometheus · uv · Docker Compose.

## Быстрый старт

```bash
make setup   # uv sync + pre-commit + git rerere
make dev     # docker compose up: api (:8000) + worker + Postgres + Redis
make test    # pytest (интеграционные тесты требуют Docker — testcontainers)
make lint    # ruff + mypy strict + import-linter
make migrate # alembic upgrade heads (все ветки)
```

Конфигурация — только через окружение: скопируйте `.env.example` в `.env`.
Секретов в репозитории нет и быть не может (gitleaks в CI).

### Команды без make (Windows)

```powershell
uv sync
uv run pytest
uv run ruff format --check .; uv run ruff check .
uv run mypy app shared migrations tests
uv run python -m migrations.cli upgrade heads
docker compose up --build
```

## Структура

```
app/           композиционный корень: конфиг, логи, middleware, /health /ready /metrics, worker
core/          модули ядра (Фазы 2–4): auth, tenants, billing, notifications, audit, admin
modules/       бизнес-модули (Фаза 6+): commerce (products, cart, orders)
shared/        примитивы и базовые классы: Repository, Service, DomainError, шина событий
migrations/    только env.py + конфиг Alembic; ревизии — в папках компонентов (ADR-0008)
tools/         add-feature (Фаза 6)
tests/         зеркалит структуру кода
docs/adr/      архитектурные решения; docs/phase0/ — проектирование; docs/UPDATE.md — обновление клиентов
```

## Ключевые инварианты

- Каждая бизнес-таблица несёт `tenant_id`; изоляция — автофильтр Repository
  + RLS в Postgres (ADR-0003). Обязательный тест изоляции на каждую таблицу.
- Каждый эндпоинт декларирует права ровно одним маркером
  (`require_permission` / `authenticated_endpoint` / `public_endpoint`) —
  иначе приложение не стартует.
- Зависимости только «вниз» (`app → core → shared`); горизонталь — только
  публичные интерфейсы или события шины; чужие таблицы не читаются никогда
  (ADR-0005, enforced import-linter).
- События — `<module>.<entity>.<action>`, публикация только после commit
  (ADR-0006).
- Деньги — целые минимальные единицы + валюта (UZS без тийинов); никаких float.
