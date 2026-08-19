#!/bin/bash
# Weekly unused-Docker cleanup. Bind-mounted PDFs are not Docker volumes.
set -euo pipefail

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

log "disk before"
df -h / | head -2
docker system df || true
docker system prune -af
log "disk after"
df -h / | head -2
docker system df || true
