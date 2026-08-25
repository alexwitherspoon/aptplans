#!/bin/bash
# Perform the one-time coordinated pre-production domain cutover.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_USER="${APP_USER:-aptplans}"
ENV_FILE="/home/${APP_USER}/.env.production"
ENV_SECRETS="/home/${APP_USER}/.env.secrets"
ENV_SEARCH="/home/${APP_USER}/.env.search"
BACKUP_ROOT="/var/backups/aptplans"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_ROOT}/pre-domain-${STAMP}"

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

echo "Stopping domain writers and readers"
"${COMPOSE[@]}" stop worker review site

echo "Saving offline pre-cutover state in ${BACKUP_DIR}"
install -d -m 0750 "${BACKUP_DIR}"
if [ -f /var/lib/aptplans/queue/jobs.sqlite3 ]; then
    cp --preserve=mode,timestamps \
        /var/lib/aptplans/queue/jobs.sqlite3* "${BACKUP_DIR}/"
fi
if [ -f /var/lib/aptplans/control/control.sqlite3 ]; then
    cp --preserve=mode,timestamps \
        /var/lib/aptplans/control/control.sqlite3* "${BACKUP_DIR}/"
fi
tar -C /var/lib/aptplans -czf "${BACKUP_DIR}/catalog.tgz" catalog

echo "Importing legacy catalog into the domain ledger"
"${COMPOSE[@]}" run --rm --no-deps worker \
    python3 -m pipeline.domain_cutover \
    /var/lib/aptplans/catalog \
    --queue-dir /var/lib/aptplans/queue \
    --confirm-preproduction-cutover

echo "Building the first complete domain release"
"${COMPOSE[@]}" up -d search
"${COMPOSE[@]}" run --rm --no-deps worker python3 -c \
    'from pipeline.site_build import run_site_build; result = run_site_build(); print(result); raise SystemExit(result not in {"built", "unchanged"})'

echo "Restarting the complete stack"
"${SCRIPT_DIR}/remote-deploy.sh"
