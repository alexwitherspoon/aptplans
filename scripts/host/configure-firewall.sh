#!/bin/bash
# Declarative host firewall: SSH, HTTP, HTTPS only.
# Does not reset UFW (that would drop the deploy SSH session).
set -euo pipefail

SSH_PORT="${SSH_PORT:-22}"
HTTP_PORT="${HTTP_PORT:-80}"
HTTPS_PORT="${HTTPS_PORT:-443}"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

if ! command -v ufw >/dev/null 2>&1; then
    echo "ufw is not installed" >&2
    exit 1
fi

ensure_allow() {
    local spec=$1
    local comment=$2
    if $SUDO ufw status | grep -Eq "^${spec}[[:space:]]+ALLOW"; then
        echo "allow ${spec} (${comment}) already present"
    else
        $SUDO ufw allow "${spec}" comment "${comment}"
    fi
}

$SUDO ufw default deny incoming
$SUDO ufw default allow outgoing
ensure_allow "${SSH_PORT}/tcp" "SSH"
ensure_allow "${HTTP_PORT}/tcp" "HTTP (Caddy)"
ensure_allow "${HTTPS_PORT}/tcp" "HTTPS (Caddy)"

if ! $SUDO ufw status | grep -q "Status: active"; then
    echo "y" | $SUDO ufw --force enable
fi

$SUDO ufw status verbose
