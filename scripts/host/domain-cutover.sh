#!/bin/bash
# Perform the one-time coordinated pre-production domain cutover.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_USER="${APP_USER:-aptplans}"
ENV_FILE="/home/${APP_USER}/.env.production"
ENV_SECRETS="/home/${APP_USER}/.env.secrets"
ENV_SEARCH="/home/${APP_USER}/.env.search"

if [ "${1:-}" != "--reset-preproduction" ]; then
    echo "refusing destructive reset without --reset-preproduction" >&2
    exit 2
fi

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

echo "Stopping the pre-production stack"
"${COMPOSE[@]}" down --remove-orphans

echo "Resetting disposable domain, control, release, and search state"
rm -rf \
    /var/lib/aptplans/queue \
    /var/lib/aptplans/control \
    /var/lib/aptplans/releases \
    /var/lib/aptplans/search
install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" \
    /var/lib/aptplans/queue \
    /var/lib/aptplans/releases \
    /var/lib/aptplans/search
install -d -m 0750 -o "${APP_USER}" -g "${APP_USER}" \
    /var/lib/aptplans/control

echo "Importing the Oregon catalog into a clean domain ledger"
"${COMPOSE[@]}" run --rm --no-deps worker \
    python3 -m pipeline.domain_cutover \
    /var/lib/aptplans/catalog \
    --queue-dir /var/lib/aptplans/queue \
    --state OR \
    --confirm-preproduction-cutover

echo "Building the first complete domain release"
"${COMPOSE[@]}" up -d search
"${COMPOSE[@]}" run --rm --no-deps worker python3 -c \
    'from pipeline.site_build import run_site_build; result = run_site_build(); print(result); raise SystemExit(result not in {"built", "unchanged"})'

echo "Restarting the complete stack"
"${SCRIPT_DIR}/remote-deploy.sh"
