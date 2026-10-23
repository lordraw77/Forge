# Forge – developer and release entry points. Complex logic lives in scripts/.
SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

VERSION ?= $(shell cat VERSION 2>/dev/null || echo 0.0.0-dev)
NAMESPACE ?= docker.io/lordraw
# MODELS=embedded (default: model files baked into the images, works air-gapped) or
# MODELS=download (`-slim` images that download Docling models and tokenizers at start).
MODELS ?= embedded
COMPOSE := docker compose -f deploy/compose/docker-compose.yml \
           $(if $(filter download,$(MODELS)),-f deploy/compose/models-download.yml) \
           --project-directory . --env-file .env --env-file deploy/compose/infra.env
NODE_IMAGE := node:24.22.0-trixie-slim
NPM := docker run --rm -u $$(id -u):$$(id -g) -e HOME=/tmp -v $(CURDIR)/frontend:/app -w /app $(NODE_IMAGE) npm

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_/.-]+:.*## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

## ---- Setup
.PHONY: env install
env: ## Generate .env and compose infra secrets (keeps existing files)
	@scripts/gen-env.sh

install: ## Install backend and frontend dependencies
	cd backend && uv sync
	$(NPM) ci

## ---- Quality
.PHONY: lint fmt typecheck licenses test test/unit test/integration test/e2e
lint: ## Lint backend (ruff, import contracts) and frontend
	cd backend && uv run ruff check . ../sdk && uv run ruff format --check . ../sdk && uv run lint-imports
	$(NPM) run lint

fmt: ## Format backend code
	cd backend && uv run ruff check --fix . ../sdk && uv run ruff format . ../sdk

licenses: ## Licence gate for backend runtime and frontend production dependencies
	@python3 scripts/check-licenses.py

typecheck: ## Static type check of the backend
	cd backend && uv run mypy && uv run mypy ../sdk/src

test: test/unit test/integration ## Run all tests

test/unit: ## Backend unit tests + frontend unit tests
	cd backend && uv run pytest -m "not integration"
	$(NPM) test

test/integration: ## Backend integration tests against the compose database
	@scripts/test-integration.sh

test/e2e: ## Playwright E2E tests against the running stack (make docker/run first)
	@scripts/test-e2e.sh

## ---- Code generation
.PHONY: api/client
api/client: ## Regenerate the frontend API client from the backend OpenAPI spec
	@scripts/gen-api-client.sh

## ---- Local run
.PHONY: build run stop logs ps migrate
build: docker/build ## Build all images

run: docker/run ## Start the full stack (alias of docker/run)

stop: docker/stop ## Stop the stack

logs: ## Follow logs (SERVICE=api to filter)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

ps: ## Show service status
	$(COMPOSE) ps

ADMIN_USER ?= $(shell grep -s '^FORGE_ADMIN_USERNAME=' .env | cut -d= -f2-)
ADMIN_EMAIL ?= $(shell grep -s '^FORGE_ADMIN_EMAIL=' .env | cut -d= -f2-)
.PHONY: admin/reset
admin/reset: ## Reset (or create) a Super Admin and unlock it; prompts for a temporary password
	@test -n "$(ADMIN_USER)" || { echo "set ADMIN_USER=<username>"; exit 1; }
	@read -rsp "Temporary password for $(ADMIN_USER): " pw; echo; \
	  printf '%s\n' "$$pw" | $(COMPOSE) exec -T api forge users ensure-superuser \
	    --username "$(ADMIN_USER)" --email "$(or $(ADMIN_EMAIL),$(ADMIN_USER)@localhost)" --password-stdin

.PHONY: providers/import
providers/import: ## Register lab model providers from the API keys in .env (idempotent)
	$(COMPOSE) exec -T api forge providers import-env

migrate: ## Apply database migrations
	$(COMPOSE) run --rm migrate

## ---- Docker
.PHONY: docker/build docker/run docker/stop docker/down docker/smoke
docker/build: env ## Build backend and web images
	$(COMPOSE) build --build-arg VERSION=$(VERSION)

docker/run: env ## Start the stack in the background and wait until healthy
	$(COMPOSE) up -d --wait
	@echo "Forge: $$(grep ^FORGE_PUBLIC_URL .env | cut -d= -f2)"

docker/stop: ## Stop containers, keep volumes
	$(COMPOSE) stop

docker/down: ## Remove containers and volumes (destroys local data)
	$(COMPOSE) down -v

docker/smoke: ## End-to-end smoke test against the running stack
	@scripts/smoke.sh

## ---- Observability profile (ADR-009)
.PHONY: obs/up obs/langfuse obs/down obs/smoke
obs/up: env ## Start Collector, Prometheus, Loki and Grafana (http://localhost:3000)
	$(COMPOSE) --profile observability up -d --wait otel-collector prometheus loki grafana
	@echo "Grafana: http://localhost:3000 (admin / GRAFANA_ADMIN_PASSWORD in deploy/compose/infra.env)"

obs/langfuse: env ## Observability profile plus Langfuse (http://localhost:3001); set FORGE_OTLP_ENDPOINT=http://otel-collector:4318 in .env
	docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/langfuse.yml --project-directory . \
	  --env-file .env --env-file deploy/compose/infra.env --profile observability up -d --wait \
	  otel-collector prometheus loki grafana langfuse-web
	@echo "Langfuse: http://localhost:3001 (LANGFUSE_INIT_USER_EMAIL / LANGFUSE_INIT_USER_PASSWORD in deploy/compose/infra.env)"

obs/smoke: ## OTLP metric and log reach Prometheus and Loki
	@scripts/smoke-observability.sh

obs/down: ## Stop the observability profile, keep its volumes
	$(COMPOSE) --profile observability stop otel-collector prometheus loki grafana

## ---- Spikes (Phase 0)
.PHONY: spike/e05a
spike/e05a: ## Spike E0.5a: worker crash + HITL with Temporal and LangGraph
	@scripts/spike-e05a.sh

.PHONY: spike/e05b spike/e05d spike/e05e
spike/e05b: ## Spike E0.5b: hybrid retrieval benchmark (ROWS=1000000 DIMS=384)
	@scripts/spike-e05b.sh "$(or $(ROWS),1000000)" "$(or $(DIMS),384)"

spike/e05e: ## Spike E0.5e: BM25 on OpenSearch + pgvector hybrid (ROWS=1000000 DIMS=384)
	@scripts/spike-e05e.sh "$(or $(ROWS),1000000)" "$(or $(DIMS),384)"

spike/e05d: ## Spike E0.5d: LiteLLM provider matrix, air-gapped (needs root for unshare)
	@scripts/spike-e05d.sh

## ---- Secrets vault (E3.8)
.PHONY: vault/up vault/unseal
vault/up: ## Lab OpenBao (profile vault); then scripts/vault-setup.sh
	$(COMPOSE) -f deploy/compose/vault.yml --profile vault up -d openbao

vault/unseal: ## Unseal the lab OpenBao after a restart
	@scripts/vault-setup.sh unseal

## ---- Backup and recovery (E3.7)
.PHONY: pitr/up pitr/drill pitr/restore
pitr/up: ## Database with WAL archiving and scheduled encrypted base backups (recreates the db container)
	$(COMPOSE) -f deploy/compose/pitr.yml up -d --build db backup

pitr/drill: ## Disaster-recovery drill on a throw-away database (needs the stack's s3 and `docker build -t forge-db:dev deploy/db`)
	@scripts/dr-drill.sh

pitr/restore: ## Restore the stack's database. TIME='2026-10-22 09:30:00+00' (UTC) or empty for the latest; needs CONFIRM=yes
	@scripts/pitr-restore-compose.sh "$(TIME)" "$(CONFIRM)"

## ---- Sandbox (E3.4)
.PHONY: sandbox/build sandbox/up sandbox/down
sandbox/build: ## Build the sandbox runner and the runtime image
	@scripts/sync-sandbox.sh
	$(COMPOSE) --profile sandbox build sandbox sandbox-runtime

sandbox/up: sandbox/build ## Start the sandbox runner (api and workers then need FORGE_SANDBOX_URL=http://sandbox:8090)
	@SANDBOX_DOCKER_GID=$$(stat -c %g /var/run/docker.sock) \
	  FORGE_SANDBOX_URL=http://sandbox:8090 \
	  $(COMPOSE) --profile sandbox up -d sandbox api worker-runs

sandbox/down: ## Stop the sandbox runner
	$(COMPOSE) --profile sandbox stop sandbox

## ---- Kubernetes
.PHONY: helm/lint helm/airgap
helm/lint: ## Lint and render the Helm chart for the default values and every deploy/helm/forge/ci/*.yaml
	helm lint deploy/helm/forge
	helm template forge deploy/helm/forge >/dev/null
	@for f in deploy/helm/forge/ci/*.yaml; do echo "== $$f"; helm lint deploy/helm/forge -f $$f && helm template forge deploy/helm/forge -f $$f >/dev/null; done

helm/airgap: ## Image archive + packaged chart for air-gapped installs (OUT=airgap)
	@scripts/airgap-bundle.sh "$(or $(OUT),airgap)"

## ---- Release
.PHONY: release
release: ## Build and push $(NAMESPACE)/forge-{backend,frontend}:$(VERSION)
	@scripts/release.sh "$(VERSION)" "$(NAMESPACE)"

.PHONY: dockerhub/overview
dockerhub/overview: ## Update Docker Hub overviews from deploy/dockerhub/*.md
	@scripts/dockerhub-overview.py "$(notdir $(NAMESPACE))"
