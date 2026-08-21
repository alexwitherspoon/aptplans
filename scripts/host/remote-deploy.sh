#!/bin/bash
# Apply host desired state and bring the full Compose stack up.
# Intended to run on the origin after rsync. Requires root or passwordless sudo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_USER="${APP_USER:-aptplans}"
ENV_FILE="/home/${APP_USER}/.env.production"
ENV_SECRETS="/home/${APP_USER}/.env.secrets"
ENV_SEARCH="/home/${APP_USER}/.env.search"

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
)
if [ -f "${ENV_SECRETS}" ]; then
    COMPOSE+=(--env-file "${ENV_SECRETS}")
fi
if [ -f "${ENV_SEARCH}" ]; then
    COMPOSE+=(--env-file "${ENV_SEARCH}")
fi
COMPOSE+=(
    -f docker/docker-compose.yml
    -f docker/docker-compose.prod.yml
)

echo "Building and starting site, search, worker, and Ollama"
"${COMPOSE[@]}" up -d --build --remove-orphans

echo "Waiting for VPN egress"
egress_ok=0
for _ in $(seq 1 36); do
    if "${COMPOSE[@]}" exec -T egress /gluetun-entrypoint healthcheck 2>/dev/null; then
        egress_ok=1
        break
    fi
    sleep 5
done
if [ "${egress_ok}" -ne 1 ]; then
    echo "egress did not become healthy" >&2
    "${COMPOSE[@]}" logs --tail=80 egress >&2 || true
    exit 1
fi

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
if [ ! -s /var/lib/aptplans/catalog/airports.jsonl ] || [ ! -s /var/lib/aptplans/catalog/grants.jsonl ]; then
    echo "Fetching NASR, NPIAS, and AIP grant histories (missing overlay)"
    "${COMPOSE[@]}" exec -T worker python3 -m pipeline.refresh_airports --force
fi
echo "Rebuilding HTML from git catalog plus origin overlay"
"${COMPOSE[@]}" run --rm --no-deps --no-TTY worker python3 site/build.py --out /var/lib/aptplans/site
airport_count="$(python3 -c "import json; print(json.load(open('/var/lib/aptplans/site/status.json'))['counts']['airports'])")"
if [ "${airport_count}" -lt 1000 ]; then
    echo "site build has ${airport_count} airports; expected NASR overlay (>=1000)" >&2
    exit 1
fi
echo "site build ok (${airport_count} airports)"
echo "deploy complete"
