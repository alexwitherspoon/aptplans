#!/bin/bash
# Apply host desired state and bring the full Compose stack up.
# Intended to run on the origin after rsync. Requires root or passwordless sudo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_USER="${APP_USER:-aptplans}"
ENV_FILE="/home/${APP_USER}/.env.production"

"${SCRIPT_DIR}/bootstrap.sh"

if [ -f "${ENV_FILE}" ]; then
    set -a
    # shellcheck source=/dev/null
    . "${ENV_FILE}"
    set +a
fi

cd "${REPO_ROOT}"

COMPOSE=(
    docker compose
    --env-file "${ENV_FILE}"
    -f docker/docker-compose.yml
    -f docker/docker-compose.prod.yml
)

echo "Building and starting site, worker, and Ollama"
"${COMPOSE[@]}" up -d --build --remove-orphans

echo "Waiting for Caddy"
caddy_ok=0
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null http://127.0.0.1/; then
        echo "origin http://127.0.0.1/ ok"
        caddy_ok=1
        break
    fi
    sleep 2
done
if [ "${caddy_ok}" -ne 1 ]; then
    echo "Caddy did not become ready on :80" >&2
    "${COMPOSE[@]}" logs --tail=80 site >&2 || true
    exit 1
fi

echo "Provisioning Ollama model"
"${SCRIPT_DIR}/provision-ollama.sh"
echo "deploy complete"
