# AptPlans local development and site build
#
# See docs/LOCAL_SETUP.md and docs/TESTING.md

.PHONY: help site test test-unit dev up stack down down-clean build clean pipeline worker links model llm

COMPOSE := docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml
COMPOSE_PROD := docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml
PY ?= python3
HOST ?= 127.0.0.1
PORT ?= 8080
export FILES_PATH ?= $(CURDIR)/data/files
export QUEUE_PATH ?= $(CURDIR)/data/queue
export CATALOG_OVERLAY_PATH ?= $(CURDIR)/data/catalog
export MODELS_PATH ?= $(CURDIR)/data/models
export TEXT_PATH ?= $(CURDIR)/data/text
export SEARCH_PATH ?= $(CURDIR)/data/search

help: ## Show this help message
	@echo ''
	@echo 'AptPlans'
	@echo '========'
	@echo ''
	@echo 'Development:'
	@grep -E '^(site|dev|up|stack|down|down-clean|pipeline|worker|links|model|llm):.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'
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

dev: ## Watch sources, rebuild dist/, and serve at http://127.0.0.1:8080
	$(PY) scripts/devserve.py --host $(HOST) --port $(PORT) --out dist

up: site ## Build the site and start local Caddy (Docker)
	$(COMPOSE) up --build site

stack: site ## Build the site and start local Caddy, search, worker, and Ollama
	mkdir -p data/files data/queue data/catalog data/models data/text data/search
	$(COMPOSE) up --build

down: ## Stop local Docker services
	$(COMPOSE) down

down-clean: ## Stop local Docker services and remove named volumes
	$(COMPOSE) down -v

build: ## Build Docker images
	$(COMPOSE) build

pipeline: worker ## Run one serial worker job

worker: ## Run one serial worker job (does not start Ollama)
	mkdir -p data/files data/queue data/catalog data/text
	$(COMPOSE) run --rm --no-deps worker python3 pipeline/run_once.py

links: ## Check due official URLs (no live FAA, no Ollama)
	mkdir -p data/files data/queue data/catalog
	$(COMPOSE) run --rm --no-deps worker python3 -m pipeline.check

model: ## Download origin Bonsai GGUF and ollama create bonsai-27b
	mkdir -p "$(MODELS_PATH)"
	$(PY) scripts/local_ollama.py download
	$(COMPOSE) up -d ollama
	$(PY) scripts/local_ollama.py create

llm: ## Warm local Bonsai and exercise LLM code paths (needs make model)
	mkdir -p data/files data/queue data/catalog data/models
	$(COMPOSE) up -d ollama
	$(PY) scripts/local_ollama.py smoke

clean: ## Remove generated site output
	rm -rf dist
