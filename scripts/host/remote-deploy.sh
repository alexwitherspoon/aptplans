#!/bin/bash
# Apply host desired state and bring the site container up.
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

echo "Building site image and starting Caddy"
docker compose \
    --env-file "${ENV_FILE}" \
    -f docker/docker-compose.yml \
    -f docker/docker-compose.prod.yml \
    up -d --build --remove-orphans site

echo "Waiting for Caddy"
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null http://127.0.0.1/; then
        echo "origin http://127.0.0.1/ ok"
        exit 0
    fi
    sleep 2
done

echo "Caddy did not become ready on :80" >&2
docker compose \
    --env-file "${ENV_FILE}" \
    -f docker/docker-compose.yml \
    -f docker/docker-compose.prod.yml \
    logs --tail=80 site >&2 || true
exit 1
