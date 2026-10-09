# KitchenTab task runner. Run from the repo root in a POSIX shell (Git Bash on Windows).
#
#   make dev                      compose stack + API + worker + web
#   make lint test                every commit
#   make test-sync [PROFILE=vercel]  SYNC suite x200
#   make e2e e2e-multi [PROFILE=vercel] [TARGET=local-vercel]
#   make test-all                 the release gate (fails while any suite is still PENDING)
#
# PROFILE=local|vercel picks adapters (ADR-0017). TARGET=local|local-vercel|prod picks the
# server that browser suites and launch-check run against.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PROFILE ?= local
TARGET ?= local
SCENARIO ?= apt4b
SYNC_ITERATIONS ?= 200

API := apps/api
WEB := apps/web
UV := uv run --project $(API)
COMPOSE := docker compose -f infra/docker-compose.yml
PY_TEST := cd $(API) && PROFILE=$(PROFILE) uv run pytest
# Playwright browsers live in the repo (ADR-0019: Firefox can't start from %LOCALAPPDATA% on Windows).
PW := PLAYWRIGHT_BROWSERS_PATH="$$PWD/.cache/ms-playwright" PROFILE=$(PROFILE) TARGET=$(TARGET)

# Suites whose tests land in a later milestone. `make test-all` refuses to pass while any remain.
PENDING ?= test-push:M9 test-chaos:M10 test-load:M10 test-security:M10 test-visual:M10 \
           test-extract:M5 launch-check:P11 restore-drill:M10

.PHONY: help
help:
	@grep -E '^[a-z][a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-22s %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup and dev
.PHONY: install browsers
install: ## Install Python and Node dependencies
	cd $(API) && uv sync
	pnpm install --frozen-lockfile
browsers: ## Install Playwright's WebKit, Chromium and Firefox into .cache/ms-playwright
	$(PW) pnpm -C $(WEB) exec playwright install chromium webkit firefox

.PHONY: up down
up: ## Start the local docker-compose stack
	$(COMPOSE) up -d --build --wait
down: ## Stop the local stack
	$(COMPOSE) down

.PHONY: migrate
migrate: ## Apply Alembic migrations to DATABASE_URL (never from a request; ADR-0011)
	cd $(API) && uv run alembic upgrade head

.PHONY: dev dev-api dev-worker dev-web
dev: up migrate ## Compose stack + API + worker + web (Ctrl-C stops the processes)
	$(MAKE) -j3 dev-api dev-worker dev-web
dev-api:
	cd $(API) && PROFILE=$(PROFILE) uv run uvicorn --factory app.main:create_app --reload --port 8000
dev-worker:
	cd $(API) && uv run arq app.workers.worker.WorkerSettings
dev-web:
	pnpm -C $(WEB) dev

.PHONY: seed
seed: migrate ## Reset the local database and load SCENARIO (default apt4b)
	cd $(API) && uv run python -m app.seed $(SCENARIO)

.PHONY: gen
gen: ## Regenerate OpenAPI and the TypeScript client
	cd $(API) && uv run python -m app.openapi > ../../$(WEB)/lib/api/openapi.json
	pnpm -C $(WEB) gen:api

# ---------------------------------------------------------------- quality gates
.PHONY: lint lint-api lint-web
lint: lint-api lint-web ## ruff, mypy, eslint, tsc, prettier
lint-api:
	cd $(API) && uv run ruff check . && uv run ruff format --check . && uv run mypy app fakeservices tests
	$(UV) ruff check scripts && $(UV) ruff format --check scripts
lint-web:
	pnpm -C $(WEB) lint
	pnpm -C $(WEB) typecheck

.PHONY: test test-api test-web test-scripts check-fixtures check-traceability
test: check-fixtures test-scripts test-api test-web check-traceability ## Unit + integration, backend and frontend
test-api:
	$(PY_TEST) -q
test-web:
	pnpm -C $(WEB) test
test-scripts:
	$(UV) pytest -q -p no:cacheprovider scripts/tests
check-fixtures: ## Money fixtures and the corpus digest match the reference (never rewrites them)
	$(UV) python docs/reference/compute_shares_reference.py --check
	$(UV) python packages/fixtures/money_corpus.py --check
check-traceability: ## FR / TEST_PLAN / edge-case coverage for done milestones
	$(UV) python scripts/check_traceability.py

.PHONY: test-sync
test-sync: ## Concurrency and race suite, SYNC_ITERATIONS (default 200) each
	cd $(API) && PROFILE=$(PROFILE) SYNC_ITERATIONS=$(SYNC_ITERATIONS) SYNC_24_SECONDS=30 uv run pytest -q -m sync

.PHONY: e2e e2e-multi test-a11y
e2e: ## Playwright journeys on WebKit iPhone 14, Chromium Pixel 7, Firefox desktop
	$(PW) pnpm -C $(WEB) e2e
e2e-multi: ## Multi-roommate journeys (3-4 browser contexts at once)
	$(PW) pnpm -C $(WEB) e2e --grep @multi
test-a11y: ## axe on every screen in both themes
	$(PW) pnpm -C $(WEB) e2e --grep "A11Y|@a11y"

# ---------------------------------------------------------------- suites still to be built
.PHONY: test-push test-chaos test-load test-security test-visual test-extract golden launch-check restore-drill
test-push test-chaos test-load test-security test-visual test-extract launch-check restore-drill:
	@echo "PENDING: '$@' is built in $(lastword $(subst :, ,$(filter $@:%,$(PENDING))))"
golden: test-extract ## Alias of test-extract

.PHONY: check-no-pending
check-no-pending:
	@if [ -n "$(strip $(PENDING))" ]; then echo "suites still pending: $(PENDING)"; exit 1; fi

# ---------------------------------------------------------------- release gate
.PHONY: test-all
test-all: lint test test-sync e2e e2e-multi test-a11y test-push test-extract test-chaos test-load \
          test-security test-visual restore-drill ## Everything, then the release gate
	$(MAKE) test-sync PROFILE=vercel
	$(MAKE) e2e-multi PROFILE=vercel
	$(UV) python scripts/check_traceability.py --all
	$(MAKE) check-no-pending
