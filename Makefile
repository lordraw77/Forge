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
.PHONY: lint fmt typecheck test test/unit test/integration
lint: ## Lint backend (ruff, import contracts) and frontend
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run lint-imports
	$(NPM) run lint

fmt: ## Format backend code
	cd backend && uv run ruff check --fix . && uv run ruff format .

typecheck: ## Static type check of the backend
	cd backend && uv run mypy

test: test/unit test/integration ## Run all tests

test/unit: ## Backend unit tests + frontend unit tests
	cd backend && uv run pytest -m "not integration"
	$(NPM) test

test/integration: ## Backend integration tests against the compose database
	@scripts/test-integration.sh

## ---- Local run
.PHONY: build run stop logs ps migrate
build: docker/build ## Build all images

run: docker/run ## Start the full stack (alias of docker/run)

stop: docker/stop ## Stop the stack

logs: ## Follow logs (SERVICE=api to filter)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

ps: ## Show service status
	$(COMPOSE) ps

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

## ---- Release
.PHONY: release
release: ## Build and push $(NAMESPACE)/forge-{backend,frontend}:$(VERSION)
	@scripts/release.sh "$(VERSION)" "$(NAMESPACE)"

.PHONY: dockerhub/overview
dockerhub/overview: ## Update Docker Hub overviews from deploy/dockerhub/*.md
	@scripts/dockerhub-overview.py "$(notdir $(NAMESPACE))"
