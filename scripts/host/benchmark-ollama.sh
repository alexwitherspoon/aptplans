#!/bin/bash
# Manual origin throughput check. Do not call from CD.
# Ollama is serial; the document worker may be generating.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_USER="${APP_USER:-aptplans}"
ENV_FILE="${ENV_FILE:-/home/${APP_USER}/.env.production}"
ENV_SECRETS="${ENV_SECRETS:-/home/${APP_USER}/.env.secrets}"
ENV_SEARCH="${ENV_SEARCH:-/home/${APP_USER}/.env.search}"

COMPOSE=(
    docker compose
    --env-file "${ENV_FILE}"
)
if [ -f "${ENV_SECRETS}" ]; then
    COMPOSE+=(--env-file "${ENV_SECRETS}")
fi
if [ -f "${ENV_SEARCH}" ]; then
    COMPOSE+=(--env-file "${ENV_SEARCH}")
fi
COMPOSE+=(
    -f "${REPO_ROOT}/docker/docker-compose.yml"
    -f "${REPO_ROOT}/docker/docker-compose.prod.yml"
)

if [ ! -f "${ENV_FILE}" ]; then
    echo "missing ${ENV_FILE}" >&2
    exit 1
fi

cd "${REPO_ROOT}"
docker inspect aptplans-ollama-1 --format 'ollama cpuset={{.HostConfig.CpusetCpus}}'
"${COMPOSE[@]}" exec -T worker python3 -m pipeline.benchmark
