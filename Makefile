# AptPlans local development and site build
#
# See docs/LOCAL_SETUP.md and docs/TESTING.md

.PHONY: help site test test-unit ci dev up stack down down-clean build clean pipeline worker links model llm eval-search eval-evidence train-evidence review-api pull-outcomes ledger-integrity ledger-backup oregon-benchmark ocr-benchmark

COMPOSE_ENV_FILE := $(wildcard .env)
COMPOSE := docker compose $(if $(COMPOSE_ENV_FILE),--env-file .env,) -f docker/docker-compose.yml -f docker/docker-compose.local.yml
COMPOSE_EGRESS := docker compose $(if $(COMPOSE_ENV_FILE),--env-file .env,) -f docker/docker-compose.yml -f docker/docker-compose.local.yml -f docker/docker-compose.egress-local.yml
PY ?= python3
HOST ?= 127.0.0.1
PORT ?= 8080
export FILES_PATH ?= $(CURDIR)/data/files
export PUBLIC_FILES_PATH ?= $(CURDIR)/data/public-files
export QUEUE_PATH ?= $(CURDIR)/data/queue
export CATALOG_OVERLAY_PATH ?= $(CURDIR)/data/catalog
export MODELS_PATH ?= $(CURDIR)/data/models
export TEXT_PATH ?= $(CURDIR)/data/text
export EXTRACTIONS_PATH ?= $(CURDIR)/data/extractions
export SEARCH_PATH ?= $(CURDIR)/data/search
export REJECT_PATH ?= $(CURDIR)/data/reject
export LOGS_PATH ?= $(CURDIR)/data/logs

help: ## Show this help message
	@echo ''
	@echo 'AptPlans'
	@echo '========'
	@echo ''
	@echo 'Development:'
	@grep -E '^(site|dev|up|stack|down|down-clean|pipeline|worker|links|model|llm):.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'
	@echo ''
	@echo 'Testing:'
	@grep -E '^(test|test-unit|ci|eval-search|eval-evidence|train-evidence|review-api|pull-outcomes|oregon-benchmark|ocr-benchmark):.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'
	@echo ''
	@echo 'Build & cleanup:'
	@grep -E '^(build|clean|ledger-integrity|ledger-backup):.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'
	@echo ''
	@echo 'See docs/LOCAL_SETUP.md for setup'
	@echo ''

site: ## Build the static site into dist/
	$(PY) site/build.py --out dist

ledger-integrity: ## Verify SQLite job and control ledgers
	$(PY) -m pipeline.ledger_ops --queue-dir "$(QUEUE_PATH)" integrity

ledger-backup: ## Back up ledgers to LEDGER_BACKUP_DIR
	@test -n "$(LEDGER_BACKUP_DIR)" || (echo "set LEDGER_BACKUP_DIR" >&2; exit 2)
	$(PY) -m pipeline.ledger_ops --queue-dir "$(QUEUE_PATH)" backup "$(LEDGER_BACKUP_DIR)"

oregon-benchmark: ## Run frozen Oregon substrate gate and report known corpus gaps
	$(PY) -m pipeline.oregon_benchmark --full $(if $(OREGON_BENCHMARK_REPORT),--output "$(OREGON_BENCHMARK_REPORT)",)

ocr-benchmark: ## Benchmark Brookings OCR in the worker container
	$(COMPOSE) run --rm worker python3 -m pipeline.ocr_benchmark $(if $(OCR_BENCHMARK_REPORT),--output "$(OCR_BENCHMARK_REPORT)",)

test-unit: ## Run unit tests only (reference fixtures via tests/conftest.py)
	$(PY) -m pytest tests -q

ci: test-unit ## CI entry point: pytest, then a fixture site build (GitHub Actions)
	APTPLANS_DEV_PREVIEW=1 $(PY) site/build.py --out dist

test: ci ## Alias for make ci

eval-search: ## Replay the adaptive search ladder against fixtures (no network, not a publish)
	$(PY) scripts/eval_search_plan.py --catalog

eval-evidence: ## Replay evidence weights against committed full gold sources (no network, not a publish)
	$(PY) scripts/eval_evidence.py --committed

train-evidence: ## Fit/eval evidence weights on full gold sources (local cache, not CI, not a publish)
	$(PY) scripts/train_evidence.py

review-api: ## Origin-only outcomes API on 127.0.0.1:8787 (token from .env.review)
	$(PY) -m pipeline.review_api

pull-outcomes: ## Pull review buckets into data/score/review (needs .env.review)
	$(PY) scripts/pull_outcomes.py

dev: ## Watch sources, rebuild dist/, and serve at http://127.0.0.1:8080
	APTPLANS_DEV_PREVIEW=1 $(PY) scripts/devserve.py --host $(HOST) --port $(PORT) --out dist

up: site ## Build the site and start local Caddy (Docker)
	$(COMPOSE) up --build site

stack: site ## Build the site and start local Caddy, search, worker, and Ollama
	mkdir -p data/files data/public-files data/queue data/catalog data/models data/text data/search data/reject
	$(COMPOSE) up --build

stack-egress: site ## Like stack, but worker scrapes through PIA VPN egress (needs .env VPN creds)
	mkdir -p data/files data/public-files data/queue data/catalog data/models data/text data/search data/reject
	$(COMPOSE_EGRESS) up --build

down: ## Stop local Docker services
	-$(COMPOSE) down
	-$(COMPOSE_EGRESS) down

down-clean: ## Stop local Docker services and remove named volumes
	$(COMPOSE) down -v

build: ## Build Docker images
	$(COMPOSE) build

pipeline: worker ## Run one serial worker job

worker: ## Run one serial worker job (does not start Ollama)
	mkdir -p data/files data/public-files data/queue data/catalog data/text data/reject
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
