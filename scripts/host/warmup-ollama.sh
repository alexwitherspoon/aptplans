#!/bin/bash
# Load bonsai-27b into Ollama and keep it resident (keep_alive -1).
# Safe to run when the model is not installed yet (exits 0).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_USER="${APP_USER:-aptplans}"
ENV_FILE="${ENV_FILE:-/home/${APP_USER}/.env.production}"
ENV_SECRETS="${ENV_SECRETS:-/home/${APP_USER}/.env.secrets}"
ENV_SEARCH="${ENV_SEARCH:-/home/${APP_USER}/.env.search}"
CONFIG="${REPO_ROOT}/config/ollama.json"

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

if [ ! -f "${ENV_FILE}" ] || [ ! -f "${CONFIG}" ]; then
    echo "warmup skipped (env or config missing)"
    exit 0
fi

set -a
# shellcheck source=/dev/null
. "${ENV_FILE}"
set +a

model="$(jq -r '.model' "${CONFIG}")"
cd "${REPO_ROOT}"

echo "Waiting for Ollama (warmup)"
ready=0
for _ in $(seq 1 90); do
    if "${COMPOSE[@]}" exec -T ollama ollama list >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
if [ "${ready}" -ne 1 ]; then
    echo "warmup skipped (Ollama not ready)"
    exit 0
fi

if ! "${COMPOSE[@]}" exec -T ollama ollama list | grep -Eq "^${model}[[:space:]:]"; then
    echo "warmup skipped (${model} not installed yet)"
    exit 0
fi

echo "Loading ${model} with keep_alive=-1"
"${COMPOSE[@]}" exec -T worker python3 -c "from pipeline.ollama import load_model; load_model()"
echo "ollama model ${model} warm"
