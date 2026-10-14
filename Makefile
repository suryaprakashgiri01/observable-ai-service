# --native-tls makes uv trust the OS certificate store, so it also works behind
# a TLS-intercepting proxy. Harmless everywhere else.
UV := uv
UVFLAGS := --native-tls
RUN := $(UV) run $(UVFLAGS)

APP_URL ?= http://localhost:8000
# The demo user's key; `make seed` prints it, or pin it with SEED_DEMO_API_KEY in .env.
# Recipes that send it start with @ so make does not echo the key to the terminal.
API_KEY ?= $(or $(SEED_DEMO_API_KEY),$(shell sed -n 's/^SEED_DEMO_API_KEY=//p' .env 2>/dev/null | tail -1))

# How long `make up` waits for postgres before giving up and showing its logs.
POSTGRES_WAIT_SECONDS ?= 60

# Container tooling is detected, not assumed. On many machines `docker` is a
# shell alias (commonly to podman) rather than a binary - and aliases do not
# exist in the /bin/sh that make runs recipes in.
# Override either on the command line: make up CONTAINER=podman
CONTAINER ?= $(shell command -v docker 2>/dev/null || command -v podman 2>/dev/null)

# Probe the compose SUBCOMMAND, not just the binary: `podman compose` shells out
# to an external provider, so podman being installed proves nothing.
# Docker first, Podman as the fallback:
#   1. `docker compose`  - Docker Desktop / Docker Engine with the compose plugin
#   2. `docker-compose`  - standalone Compose v2 (also drives Podman through its docker socket)
#   3. `podman compose`  - Podman only; it points its provider at podman's own socket
COMPOSE ?= $(shell \
	if docker compose version >/dev/null 2>&1; then echo "docker compose"; \
	elif docker-compose version >/dev/null 2>&1; then echo "docker-compose"; \
	elif podman compose version >/dev/null 2>&1; then echo "podman compose"; \
	fi)

# `podman compose` prints an "Executing external compose provider" banner on every call.
export PODMAN_COMPOSE_WARNING_LOGS := false

define require_compose
	@test -n "$(COMPOSE)" || { \
		echo "No working Compose implementation found."; \
		echo "Install Docker Desktop, or Podman plus a compose provider:"; \
		echo "    macOS:   brew install podman docker-compose"; \
		echo "    Windows: winget install RedHat.Podman Docker.DockerCompose  (or Docker Desktop)"; \
		echo "    Linux:   your package manager, or pip install podman-compose"; \
		echo "Already have one? Point make at it: make $@ COMPOSE='docker-compose'"; \
		exit 1; \
	}
endef

.PHONY: help runtime psql install dev up up-infra down logs lint format typecheck test test-cov test-ci check \
        migrate migration downgrade seed docker-build clean demo-success demo-invalid demo-fault

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

runtime: ## Show the container runtime and compose command make detected
	@echo "container: $(or $(CONTAINER),<none found>)"
	@echo "compose:   $(or $(COMPOSE),<none found>)"

install: ## Create the venv and install runtime + dev dependencies
	$(UV) sync --all-groups $(UVFLAGS)
	$(RUN) pre-commit install

dev: ## Run the API with auto-reload on :8000 (needs `make up-infra`)
	RELOAD=true $(RUN) python -m app.main

up: ## Start the full stack: app, postgres, jaeger, prometheus, grafana
	$(require_compose)
	$(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory _wait-postgres

up-infra: ## Start only backing services (run the app with `make dev`)
	$(require_compose)
	$(COMPOSE) up -d postgres jaeger prometheus grafana
	@$(MAKE) --no-print-directory _wait-postgres

_wait-postgres:
	@printf 'waiting for postgres'; \
	for i in $$(seq 1 $(POSTGRES_WAIT_SECONDS)); do \
		if $(COMPOSE) exec -T postgres pg_isready -U app -d ai_service >/dev/null 2>&1; then \
			echo " ready on localhost:$${POSTGRES_HOST_PORT:-5433}"; exit 0; \
		fi; \
		printf '.'; sleep 1; \
	done; \
	echo; echo "postgres did not become ready within $(POSTGRES_WAIT_SECONDS)s." >&2; \
	$(COMPOSE) logs --tail=20 postgres >&2 || true; \
	exit 1

down: ## Stop the stack
	$(require_compose)
	$(COMPOSE) down

logs: ## Tail stack logs (one service: make logs s=app)
	$(require_compose)
	$(COMPOSE) logs -f $(s)

psql: ## Open a psql shell in the postgres container
	$(require_compose)
	$(COMPOSE) exec postgres psql -U app -d ai_service

lint: ## Lint
	$(RUN) ruff check .
	$(RUN) ruff format --check .

format: ## Auto-format and auto-fix
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

typecheck: ## Strict type check
	$(RUN) mypy app scripts

test: ## Unit + API tests (no infrastructure needed)
	$(RUN) pytest -m "not integration"

test-cov: ## All tests with coverage (fails under 80%)
	$(RUN) pytest --cov=app --cov-report=term-missing

test-ci: ## Tests exactly as CI runs them, with the coverage gate
	$(RUN) pytest -m "not integration" --cov=app --cov-report=term-missing:skip-covered --cov-report=xml:coverage.xml

check: lint typecheck test-ci ## Everything CI runs. Use before opening a PR.

migration: ## Autogenerate a migration: make migration name="add x"
	@test -n "$(name)" || (echo 'usage: make migration name="describe the change"' && exit 1)
	$(RUN) alembic revision --autogenerate -m "$(name)"

migrate: ## Apply migrations
	$(RUN) alembic upgrade head

downgrade: ## Roll back one migration
	$(RUN) alembic downgrade -1

seed: ## Seed the model catalog and the initial users (idempotent)
	$(RUN) python -m scripts.seed_models
	$(RUN) python -m scripts.seed_users

docker-build: ## Build the container image
	@test -n "$(CONTAINER)" || { echo "No container runtime found (docker/podman)."; exit 1; }
	$(CONTAINER) build -t observable-ai-service:local .

demo-success: ## Demo: a request that creates a ticket
	@curl -sS -X POST $(APP_URL)/ask -H 'Content-Type: application/json' \
		-H "X-API-Key: $(API_KEY)" -H 'X-Request-ID: demo-success-1' \
		-d '{"question":"Create a high priority ticket: checkout page is slow"}' | python3 -m json.tool

demo-invalid: ## Demo: an invalid request (empty question)
	@curl -sS -X POST $(APP_URL)/ask -H 'Content-Type: application/json' \
		-H "X-API-Key: $(API_KEY)" -H 'X-Request-ID: demo-invalid-1' \
		-d '{"question":""}' | python3 -m json.tool

demo-fault: ## Demo: an LLM timeout that retries, then falls back
	@curl -sS -X POST $(APP_URL)/ask -H 'Content-Type: application/json' \
		-H "X-API-Key: $(API_KEY)" -H 'X-Request-ID: demo-fault-1' -H 'X-Debug-Fault: llm_timeout' \
		-d '{"question":"Give me a revenue report for last 7 days"}' | python3 -m json.tool

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov coverage.xml .coverage
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
