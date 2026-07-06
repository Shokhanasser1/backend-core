# Все команды проекта — одной строкой каждая (мастер-промпт).
# Windows без make: используйте эквиваленты из README («Команды без make»).

.DEFAULT_GOAL := help
UV ?= uv

setup: ## Зависимости + pre-commit + git rerere (bootstrap разработчика)
	$(UV) sync
	$(UV) run pre-commit install
	git config rerere.enabled true

dev: ## Поднять весь стек: API + worker + Postgres + Redis
	docker compose up --build

test: ## Тесты (интеграционные требуют Docker: testcontainers)
	$(UV) run pytest

lint: ## Формат-чек, линт, mypy strict, контракты импортов
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	$(UV) run mypy app shared migrations tests
	$(UV) run lint-imports

fmt: ## Автоформатирование + автофиксы линта
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

migrate: ## Накатить ВСЕ головы миграций (строго upgrade heads — мультиветочный Alembic)
	$(UV) run python -m migrations.cli upgrade heads

revision: ## Новая ревизия: make revision ARGS='-m "msg" --head shared@head --version-path shared/migrations'
	$(UV) run python -m migrations.cli revision $(ARGS)

seed: ## Сид-данные (в Фазе 1 отсутствуют; справочник валют приедет с billing в Фазе 3)
	@echo "Nothing to seed in Phase 1 (currencies seed arrives with billing, Phase 3)"

help: ## Список целей
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-10s %s\n", $$1, $$2}'

.PHONY: setup dev test lint fmt migrate revision seed help
