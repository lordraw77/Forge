# Forge – developer and release entry points. Complex logic lives in scripts/.
SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

VERSION ?= $(shell cat VERSION 2>/dev/null || echo 0.0.0-dev)
NAMESPACE ?= docker.io/lordraw
COMPOSE := docker compose -f deploy/compose/docker-compose.yml --project-directory . \
           --env-file .env --env-file deploy/compose/infra.env
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
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run lint-imports
	$(NPM) run lint

fmt: ## Format backend code
	cd backend && uv run ruff check --fix . && uv run ruff format .

licenses: ## Licence gate for backend runtime and frontend production dependencies
	@python3 scripts/check-licenses.py

typecheck: ## Static type check of the backend
	cd backend && uv run mypy

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
.PHONY: obs/up obs/down obs/smoke
obs/up: env ## Start Collector, Prometheus, Loki and Grafana (http://localhost:3000)
	$(COMPOSE) --profile observability up -d --wait otel-collector prometheus loki grafana
	@echo "Grafana: http://localhost:3000 (admin / GRAFANA_ADMIN_PASSWORD in deploy/compose/infra.env)"

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

## ---- Release
.PHONY: release
release: ## Build and push $(NAMESPACE)/forge-{backend,frontend}:$(VERSION)
	@scripts/release.sh "$(VERSION)" "$(NAMESPACE)"

.PHONY: dockerhub/overview
dockerhub/overview: ## Update Docker Hub overviews from deploy/dockerhub/*.md
	@scripts/dockerhub-overview.py "$(notdir $(NAMESPACE))"
