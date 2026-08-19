# AptPlans local development and site build
#
# See docs/LOCAL_SETUP.md and docs/TESTING.md

.PHONY: help site test test-unit dev up down build clean pipeline worker

COMPOSE := docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml
COMPOSE_PROD := docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml
PY ?= python3
HOST ?= 127.0.0.1
PORT ?= 8080

help: ## Show this help message
	@echo ''
	@echo 'AptPlans'
	@echo '========'
	@echo ''
	@echo 'Development:'
	@grep -E '^(site|dev|up|down|pipeline|worker):.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'
	@echo ''
	@echo 'Testing:'
	@grep -E '^(test|test-unit):.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'
	@echo ''
	@echo 'Build & cleanup:'
	@grep -E '^(build|clean):.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'
	@echo ''
	@echo 'See docs/LOCAL_SETUP.md for setup'
	@echo ''

site: ## Build the static site into dist/
	$(PY) site/build.py --out dist

test: test-unit site ## Run tests and verify a site build

test-unit: ## Run unit tests
	$(PY) -m pytest tests -q

dev: site ## Build and serve dist/ at http://127.0.0.1:8080
	$(PY) -m http.server $(PORT) --bind $(HOST) --directory dist

up: site ## Build the site and start local Caddy (Docker)
	$(COMPOSE) up --build site

down: ## Stop local Docker services
	$(COMPOSE) down

build: ## Build Docker images
	$(COMPOSE) build

pipeline: worker ## Run one serial worker job

worker: ## Run one serial worker job (does not start Ollama)
	$(COMPOSE) run --rm --no-deps worker python3 pipeline/run_once.py

clean: ## Remove generated site output
	rm -rf dist
