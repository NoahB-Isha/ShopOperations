# Isha Life Shop Ops — developer entry points.
# `make dev` brings up the full stack; `make seed` loads the demo; `make test` runs everything.

COMPOSE := docker compose -f infra/compose.yaml --project-directory . --project-name shopops

.PHONY: help dev logs down nuke seed fixtures test test-backend test-frontend \
        lint typecheck format e2e migrate revision openapi

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env
	@echo "Created .env from .env.example (safe local defaults — fixture mode, dev auth)."

dev: .env ## Bring up the full stack (db, backend, worker, frontend)
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  Web:      http://localhost:$${WEB_PORT:-5173}"
	@echo "  API docs: http://localhost:$${API_PORT:-8000}/api/docs"
	@echo "  Health:   http://localhost:$${API_PORT:-8000}/api/v1/health"
	@echo ""
	@echo "  Next: 'make seed' to load the demo data, then log in (dev-mode codes"
	@echo "  are shown right on the login screen). 'make logs' to tail."

logs: ## Tail all service logs
	$(COMPOSE) logs -f --tail=100

down: ## Stop the stack (keeps data)
	$(COMPOSE) down

nuke: ## Stop the stack and DELETE the database volume
	$(COMPOSE) down -v

seed: ## Load demo data (zones, centers, users, 1,200 products, sales) into the running stack
	$(COMPOSE) exec backend python -m app.seeds.demo

fixtures: ## (Re)generate the deterministic demo Odoo fixtures locally
	cd backend && uv run python -m app.odoo.fixtures.generate

test: test-backend test-frontend ## Run the full test suite

test-backend: ## Backend unit + integration tests (no Docker needed)
	cd backend && uv run pytest

test-frontend: ## Frontend typecheck + unit tests
	cd frontend && npm run --silent typecheck && npm run --silent test

openapi: ## Regenerate docs/api/openapi.json from the FastAPI app
	cd backend && uv run python scripts/export_openapi.py

lint: ## Ruff + ESLint
	uv run ruff check backend worker
	cd frontend && npm run --silent lint

typecheck: ## mypy + tsc
	uv run mypy backend/app worker/worker
	cd frontend && npm run --silent typecheck

format: ## Auto-format Python
	uv run ruff format backend worker
	uv run ruff check --fix backend worker

e2e: ## Playwright smoke tests (stack must be up and seeded)
	cd frontend && npx playwright test

migrate: ## Apply DB migrations to DATABASE_URL
	cd backend && uv run alembic upgrade head

revision: ## Autogenerate a migration: make revision m="message"
	cd backend && uv run alembic revision --autogenerate -m "$(m)"
