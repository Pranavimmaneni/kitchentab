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
# Every pytest run leaves JUnit XML (with each test's TEST_PLAN ids) for scripts/test_report.py.
RESULTS := .cache/results
JUNIT = --junitxml=../../$(RESULTS)/pytest-$@-$(PROFILE)-$$$$.xml
PY_TEST = cd $(API) && PROFILE=$(PROFILE) uv run pytest $(JUNIT)
# Playwright browsers live in the repo (ADR-0019: Firefox can't start from %LOCALAPPDATA% on Windows).
# TARGET=local-vercel: Node (Playwright's API requests) trusts the rehearsal's local CA (ADR-0028).
LV_CA := .cache/local-vercel/caddy/caddy/pki/authorities/local/root.crt
PW := PLAYWRIGHT_BROWSERS_PATH="$$PWD/.cache/ms-playwright" PROFILE=$(PROFILE) TARGET=$(TARGET) \
      $(if $(filter local-vercel,$(TARGET)),NODE_EXTRA_CA_CERTS="$$PWD/$(LV_CA)")

# Suites whose tests land in a later milestone. `make test-all` refuses to pass while any remain.
PENDING ?= test-visual:M10

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

.PHONY: test test-api test-web test-scripts check-fixtures check-traceability check-invariants
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
check-invariants: ## Invariant checker against DATABASE_URL (DATA-05)
	$(UV) python scripts/check_invariants.py
check-traceability: ## FR / TEST_PLAN / edge-case coverage for done milestones
	$(UV) python scripts/check_traceability.py

.PHONY: test-sync
test-sync: ## Concurrency and race suite, SYNC_ITERATIONS (default 200) each
	cd $(API) && PROFILE=$(PROFILE) SYNC_ITERATIONS=$(SYNC_ITERATIONS) SYNC_24_SECONDS=30 uv run pytest $(JUNIT) -q -m sync

.PHONY: e2e e2e-multi test-a11y
e2e: ## Playwright journeys on WebKit iPhone 14, Chromium Pixel 7, Firefox desktop
	$(PW) pnpm -C $(WEB) e2e
e2e-multi: ## Multi-roommate journeys (3-4 browser contexts at once)
	$(PW) pnpm -C $(WEB) e2e --grep @multi
test-a11y: ## axe on every screen in both themes
	$(PW) pnpm -C $(WEB) e2e --grep "A11Y|@a11y"

.PHONY: test-push
test-push: ## Web Push end to end: the real sender + VAPID + RFC 8291 against the fake push service
	$(PY_TEST) -q tests/test_fake_push.py tests/test_notify.py
	@if ls $(WEB)/tests/e2e/*push*.spec.ts >/dev/null 2>&1; then $(PW) pnpm -C $(WEB) e2e --grep "PUSH-"; fi

.PHONY: test-extract
test-extract: ## Receipt accuracy on the 30 generated receipts (fails below the C1 targets; writes REPORT.md) + extraction tests
	cd $(API) && uv run python ../../scripts/receipts/evaluate.py
	$(PY_TEST) -q tests/extraction tests/test_receipts.py tests/test_fake_blob.py

.PHONY: test-chaos
test-chaos: ## CHAOS-03/04/05/07: connections killed mid-write, Toxiproxy, worker crash, push 500/429, extractor hang
	$(COMPOSE) up -d toxiproxy
	$(PY_TEST) -q tests/test_chaos.py

.PHONY: house
house: ## Run KitchenTab for the household on this Wi-Fi: http://<this computer>:3100 (scripts/house.py; ARGS=--skip-build)
	$(UV) python scripts/house.py $(ARGS)

.PHONY: simulate
SEED ?= 1
MINUTES ?= 30
simulate: ## Live household simulation, 4 people on 6 devices (BUILD_ALL §4.3): SEED= MINUTES= PROFILE= [TARGET=local-vercel SIM_ARGS=…]
	$(UV) python scripts/simulate_household.py --seed $(SEED) --minutes $(MINUTES) --profile $(PROFILE) --target $(TARGET) $(SIM_ARGS)

.PHONY: restore-drill
restore-drill: ## Seed, export, restore into a fresh database, compare, check invariants (DATA-02)
	cd $(API) && uv run python -m app.tools.restore_drill --export-file ../../.cache/backups/drill.jsonl.gz

# ---------------------------------------------------------------- security (TEST_PLAN 3.10, ADR-0025)
# SEC pytest files in both profiles, dependency audits, the client-bundle scan, and the ZAP baseline
# when ZAP_TARGET points at a running web app (this never starts one): the P10 run must set it.
SEC_TESTS := tests/test_policy.py tests/test_security.py tests/test_security_matrix.py \
             tests/test_security_idor.py tests/test_security_injection.py tests/test_security_secrets.py
# SEC rows proved inside feature suites (SEC-10..14).
SEC_FEATURE_TESTS := tests/test_auth.py tests/test_houses.py tests/test_notify.py \
                     tests/test_settings_and_routes.py tests/test_tabs_and_account.py
SEC_FEATURE_SELECT := SEC_ or LINK_06 or AUTH_14
ZAP_TARGET ?=
ZAP_IMAGE ?= ghcr.io/zaproxy/zaproxy:stable
ZAP_URL = $(or $(ZAP_TARGET),http://host.docker.internal:3000)

.PHONY: test-security test-security-api audit scan-bundle zap-baseline
test-security: ## SEC suite: API tests (local + vercel), audits, bundle scan, ZAP if ZAP_TARGET is set
	$(MAKE) test-security-api PROFILE=local
	$(MAKE) test-security-api PROFILE=vercel
	$(UV) pytest -q -p no:cacheprovider scripts/tests/test_scan_bundle_secrets.py scripts/tests/test_security_gates.py
	$(MAKE) scan-bundle
	$(MAKE) audit
	@if [ -n "$(ZAP_TARGET)" ]; then $(MAKE) zap-baseline; \
	else echo "test-security: ZAP baseline (SEC-08) skipped: set ZAP_TARGET to the running web app"; fi
test-security-api: ## SEC-01..07 and SEC-10..14 API tests for PROFILE
	$(PY_TEST) -q $(SEC_TESTS)
	$(PY_TEST) -q $(SEC_FEATURE_TESTS) -k "$(SEC_FEATURE_SELECT)"
audit: ## SEC-09: pip-audit (locked API env) + pnpm audit (web); fails on unwaived high/critical
	@mkdir -p .cache/audit
	cd $(API) && uv export --frozen --all-groups --format requirements-txt --no-emit-project \
	  -o ../../.cache/audit/api-requirements.txt >/dev/null
	rm -f .cache/audit/pip-audit.json .cache/audit/pnpm-audit.json
	cd $(API) && uv run pip-audit -r ../../.cache/audit/api-requirements.txt --disable-pip --require-hashes \
	  --progress-spinner off -f json -o ../../.cache/audit/pip-audit.json || true
	pnpm -C $(WEB) audit --json > .cache/audit/pnpm-audit.json || true
	$(UV) python scripts/audit_gate.py --pip .cache/audit/pip-audit.json --pnpm .cache/audit/pnpm-audit.json
scan-bundle: ## SEC-07: no server-only env names or values in apps/web/.next/static (skipped without a build)
	$(UV) python scripts/scan_bundle_secrets.py --allow-missing
zap-baseline: ## SEC-08: ZAP baseline against ZAP_TARGET (default host.docker.internal:3000); no high/medium
	@mkdir -p .cache/zap && chmod a+rwx .cache/zap
	cp infra/zap/baseline-rules.tsv .cache/zap/rules.tsv
	rm -f .cache/zap/zap-report.json
	wrk="$$(cd .cache/zap && (pwd -W 2>/dev/null || pwd))"; \
	MSYS_NO_PATHCONV=1 docker run --rm --add-host=host.docker.internal:host-gateway -v "$$wrk:/zap/wrk:rw" \
	  $(ZAP_IMAGE) zap-baseline.py -t "$(ZAP_URL)" -c rules.tsv -I \
	  -r zap-report.html -J zap-report.json -w zap-report.md || true
	$(UV) python scripts/zap_gate.py .cache/zap/zap-report.json --rules infra/zap/baseline-rules.tsv

# ---------------------------------------------------------------- suites still to be built
.PHONY: test-load test-visual golden
test-load: ## PERF-01 (k6 in Docker: 200 people on 50 bills) + PERF-02 (1,000 streams / 200 houses), then invariants
	cd $(API) && uv run pytest $(JUNIT) -q -m load tests/test_perf_load.py

test-visual:
	@echo "PENDING: '$@' is built in $(lastword $(subst :, ,$(filter $@:%,$(PENDING))))"
golden: test-extract ## Alias of test-extract

# ---------------------------------------------------------------- deploy (BUILD_ALL §6 step 3, ADR-0022)
# Morning scripts. Pass flags with ARGS, or DRY_RUN=1 to print every command and run none:
#   make deploy-setup DRY_RUN=1      make rollback ARGS="--to 2"      make set-email ARGS=--local
DEPLOY_PY := $(UV) python scripts/deploy
DEPLOY_FLAGS = $(if $(filter 1 true yes,$(DRY_RUN)),--dry-run) $(ARGS)
.PHONY: deploy-setup deploy redeploy rollback set-email prod-clean-test-data
deploy-setup: ## One-time Vercel setup: link, Neon, Blob, every env var (idempotent; DRY_RUN=1)
	$(DEPLOY_PY)/deploy_setup.py $(DEPLOY_FLAGS)
deploy: ## lint + tests, migrations on Neon, vercel deploy --prod, origin settings (DRY_RUN=1)
	$(DEPLOY_PY)/deploy.py $(DEPLOY_FLAGS)
redeploy: ## Rebuild the live production deployment with the current env vars
	$(DEPLOY_PY)/redeploy.py $(DEPLOY_FLAGS)
rollback: ## Promote an earlier production deployment (lists them; ARGS="--to N")
	$(DEPLOY_PY)/rollback.py $(DEPLOY_FLAGS)
set-email: ## Switch sign-in codes to Gmail SMTP and confirm a real code (ARGS=--local: Mailpit)
	$(DEPLOY_PY)/set_email.py $(DEPLOY_FLAGS)
prod-clean-test-data: ## Preview (or ARGS=--yes delete) launch-check test data in production
	$(DEPLOY_PY)/prod_clean_test_data.py $(DEPLOY_FLAGS)

# ---------------------------------------------------------------- local-vercel rehearsal (BUILD_ALL §6, ADR-0028)
# `make local-vercel-up`: production builds of web (next build + next start) and API
# (app.vercel_entry:app) behind a local Vercel edge at https://localhost:8443 (vercel.json routing,
# Caddy's internal CA, 4.5 MB / 300 s limits), PgBouncer, the fake Blob and push services and the
# queue devserver + subscriber. LV_SKIP_BUILD=1 reuses the last web build.
#   make launch-check TARGET=local-vercel [SSE_SECONDS=30]     make launch-check TARGET=prod
SSE_SECONDS ?=
.PHONY: local-vercel-up local-vercel-down local-vercel-status launch-check
local-vercel-up: ## Production build rehearsal on https://localhost:8443 (ADR-0028)
	$(UV) python scripts/local_vercel.py up $(if $(filter 1 true yes,$(LV_SKIP_BUILD)),--skip-build)
local-vercel-down: ## Stop the rehearsal (ARGS=--drop-db also drops the kitchentab_lv database)
	$(UV) python scripts/local_vercel.py down $(ARGS)
local-vercel-status: ## Show the rehearsal's processes
	$(UV) python scripts/local_vercel.py status
launch-check: ## Launch checks against TARGET=local-vercel|prod, then the @launch browser checks (local-vercel)
	@case "$(TARGET)" in local-vercel|prod) ;; *) echo "launch-check: set TARGET=local-vercel or TARGET=prod"; exit 2;; esac
	rc=0; $(UV) python scripts/launch_check.py --target $(TARGET) $(if $(SSE_SECONDS),--sse-seconds $(SSE_SECONDS)) $(ARGS) || rc=$$?; \
	if [ "$(TARGET)" = "local-vercel" ]; then \
	  $(PW) pnpm -C $(WEB) e2e --grep @launch --project chromium-pixel-7 || rc=1; \
	fi; exit $$rc

.PHONY: check-no-pending
check-no-pending:
	@if [ -n "$(strip $(PENDING))" ]; then echo "suites still pending: $(PENDING)"; exit 1; fi

# ---------------------------------------------------------------- release gate
.PHONY: test-all
test-all: results-clean lint test test-sync e2e e2e-multi test-a11y test-push test-extract test-chaos test-load \
          test-security test-visual restore-drill ## Everything, then the release gate
	$(MAKE) test-sync PROFILE=vercel
	$(MAKE) e2e-multi PROFILE=vercel
	$(UV) python scripts/check_traceability.py --all
	$(UV) python scripts/test_report.py
	$(MAKE) check-no-pending
.PHONY: results-clean
results-clean:
	rm -rf $(RESULTS) && mkdir -p $(RESULTS)
