#!/bin/bash
# Idempotent host bootstrap for a bare Debian 13 (trixie) origin.
# Keeps the OS thin: Docker Engine, firewall, fail2ban, unattended-upgrades.
# Caddy, Meilisearch, the worker, and Ollama run in Compose - not on the host.
#
# Run as aptplans (CD) or from console as root. Remote root SSH is disabled.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HOST_CONFIG="${REPO_ROOT}/config/host"

APP_USER="${APP_USER:-aptplans}"
REPO_DIR="${REPO_DIR:-/opt/aptplans}"
SITE_DIR="${SITE_DIR:-/var/lib/aptplans/site}"
FILES_DIR="${FILES_DIR:-/var/lib/aptplans/files}"
QUEUE_DIR="${QUEUE_DIR:-/var/lib/aptplans/queue}"
CATALOG_OVERLAY_DIR="${CATALOG_OVERLAY_DIR:-/var/lib/aptplans/catalog}"
TLS_DIR="${TLS_DIR:-/var/lib/aptplans/tls}"
OLLAMA_DIR="${OLLAMA_DIR:-/var/lib/aptplans/ollama}"
MODELS_DIR="${MODELS_DIR:-/var/lib/aptplans/models}"
TEXT_DIR="${TEXT_DIR:-/var/lib/aptplans/text}"
SEARCH_DIR="${SEARCH_DIR:-/var/lib/aptplans/search}"
REJECT_DIR="${REJECT_DIR:-/var/lib/aptplans/reject}"
LOGS_DIR="${LOGS_DIR:-/var/lib/aptplans/logs}"
EGRESS_DIR="${EGRESS_DIR:-/var/lib/aptplans/egress}"
TIMEZONE="${TIMEZONE:-America/Los_Angeles}"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

log() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

require_debian_trixie() {
    # shellcheck source=/dev/null
    . /etc/os-release
    if [ "${ID:-}" != "debian" ] || [ "${VERSION_CODENAME:-}" != "trixie" ]; then
        log "error: expected Debian 13 (trixie), got ${PRETTY_NAME:-unknown}"
        exit 1
    fi
    log "os: ${PRETTY_NAME}"
}

as_root() {
    $SUDO "$@"
}

apt_install() {
    as_root env DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get install -y "$@"
}

install_base_packages() {
    log "installing minimal host packages"
    as_root tee /etc/apt/apt.conf.d/99aptplans-norecommends >/dev/null <<'EOF'
APT::Install-Recommends "false";
APT::Install-Suggests "false";
EOF
    as_root apt-get update -qq
    apt_install \
        sudo \
        curl \
        ca-certificates \
        gnupg \
        openssl \
        rsync \
        git \
        jq \
        ufw \
        fail2ban \
        unattended-upgrades \
        apt-listchanges \
        chrony \
        needrestart
}

install_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        log "docker already installed: $(docker --version)"
        return 0
    fi
    log "installing Docker Engine from download.docker.com"
    as_root install -m 0755 -d /etc/apt/keyrings
    if [ ! -f /etc/apt/keyrings/docker.asc ]; then
        curl -fsSL https://download.docker.com/linux/debian/gpg | as_root tee /etc/apt/keyrings/docker.asc >/dev/null
        as_root chmod a+r /etc/apt/keyrings/docker.asc
    fi
    # shellcheck source=/dev/null
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
        | as_root tee /etc/apt/sources.list.d/docker.list >/dev/null
    as_root apt-get update -qq
    apt_install \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
    as_root systemctl enable --now docker
    log "docker: $(docker --version)"
    log "compose: $(docker compose version)"
}

ensure_app_user() {
    if ! id "${APP_USER}" >/dev/null 2>&1; then
        log "creating user ${APP_USER}"
        as_root useradd -m -s /bin/bash "${APP_USER}"
    fi
    as_root usermod -aG docker "${APP_USER}"
    as_root install -d -m 0750 -o "${APP_USER}" -g "${APP_USER}" "/home/${APP_USER}/.ssh"

    as_root tee /etc/sudoers.d/aptplans >/dev/null <<EOF
${APP_USER} ALL=(ALL) NOPASSWD:ALL
EOF
    as_root chmod 440 /etc/sudoers.d/aptplans
    as_root visudo -cf /etc/sudoers.d/aptplans

    seed_app_user_keys
    as_root chown "${APP_USER}:${APP_USER}" "/home/${APP_USER}/.ssh/authorized_keys"
    as_root chmod 600 "/home/${APP_USER}/.ssh/authorized_keys"
}

seed_app_user_keys() {
    local dest="/home/${APP_USER}/.ssh/authorized_keys"
    if [ -s "${dest}" ]; then
        return 0
    fi
    local src=""
    if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ] && [ "${SUDO_USER}" != "${APP_USER}" ] \
        && [ -s "/home/${SUDO_USER}/.ssh/authorized_keys" ]; then
        src="/home/${SUDO_USER}/.ssh/authorized_keys"
    elif [ "$(id -u)" -ne 0 ] && [ "$(id -un)" != "${APP_USER}" ] && [ -s "${HOME}/.ssh/authorized_keys" ]; then
        src="${HOME}/.ssh/authorized_keys"
    elif [ -s /root/.ssh/authorized_keys ]; then
        src=/root/.ssh/authorized_keys
    fi
    if [ -z "${src}" ]; then
        log "error: ${APP_USER} has no authorized_keys; add the CD public key before locking sshd"
        exit 1
    fi
    log "seeding ${APP_USER} authorized_keys from ${src}"
    as_root cp "${src}" "${dest}"
}

ensure_directories() {
    log "ensuring data directories"
    as_root install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${REPO_DIR}"
    as_root install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${SITE_DIR}"
    as_root install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${FILES_DIR}"
    as_root install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${QUEUE_DIR}"
    as_root install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${CATALOG_OVERLAY_DIR}"
    as_root install -d -m 0750 -o "${APP_USER}" -g "${APP_USER}" "${TLS_DIR}"
    as_root install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${OLLAMA_DIR}"
    as_root install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${MODELS_DIR}"
    as_root install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${TEXT_DIR}"
    as_root install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${SEARCH_DIR}"
    as_root install -d -m 0750 -o "${APP_USER}" -g "${APP_USER}" "${REJECT_DIR}"
    as_root install -d -m 0750 -o "${APP_USER}" -g "${APP_USER}" "${LOGS_DIR}"
    as_root install -d -m 0750 -o "${APP_USER}" -g "${APP_USER}" "${EGRESS_DIR}"
    as_root chown -R "${APP_USER}:${APP_USER}" "${REPO_DIR}" "${SITE_DIR}" "${TLS_DIR}" "${OLLAMA_DIR}" "${MODELS_DIR}"
    as_root chown "${APP_USER}:${APP_USER}" "${FILES_DIR}" "${QUEUE_DIR}" "${CATALOG_OVERLAY_DIR}" "${TEXT_DIR}" "${SEARCH_DIR}" "${REJECT_DIR}" "${LOGS_DIR}" "${EGRESS_DIR}"
}

ensure_origin_tls() {
    if [ -f "${TLS_DIR}/origin.pem" ] && [ -f "${TLS_DIR}/origin.key" ]; then
        log "origin TLS cert already present"
        return 0
    fi
    log "writing self-signed origin cert (replace with a Cloudflare Origin CA cert for Full Strict)"
    as_root openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
        -keyout "${TLS_DIR}/origin.key" \
        -out "${TLS_DIR}/origin.pem" \
        -days 3650 \
        -subj "/CN=aptplans.org"
    as_root chown "${APP_USER}:${APP_USER}" "${TLS_DIR}/origin.pem" "${TLS_DIR}/origin.key"
    as_root chmod 644 "${TLS_DIR}/origin.pem"
    as_root chmod 600 "${TLS_DIR}/origin.key"
}

configure_timezone() {
    as_root timedatectl set-timezone "${TIMEZONE}"
    as_root timedatectl set-ntp true
    log "timezone: $(timedatectl show -p Timezone --value)"
}

configure_sysctl() {
    as_root cp "${HOST_CONFIG}/sysctl.conf" /etc/sysctl.d/99-aptplans.conf
    as_root sysctl --system >/dev/null
}

configure_sshd() {
    as_root cp "${HOST_CONFIG}/sshd.conf" /etc/ssh/sshd_config.d/99-aptplans.conf
    as_root chmod 644 /etc/ssh/sshd_config.d/99-aptplans.conf
    as_root sshd -t
    as_root systemctl reload ssh || as_root systemctl reload sshd
}

configure_unattended_upgrades() {
    as_root cp "${HOST_CONFIG}/20auto-upgrades" /etc/apt/apt.conf.d/20auto-upgrades
    as_root cp "${HOST_CONFIG}/50unattended-upgrades" /etc/apt/apt.conf.d/50unattended-upgrades
    as_root chmod 644 /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/50unattended-upgrades
    as_root systemctl enable --now unattended-upgrades
}

configure_fail2ban() {
    as_root cp "${HOST_CONFIG}/fail2ban-sshd.conf" /etc/fail2ban/jail.d/aptplans-sshd.conf
    as_root chmod 644 /etc/fail2ban/jail.d/aptplans-sshd.conf
    as_root systemctl enable --now fail2ban
    as_root systemctl reload fail2ban || as_root systemctl restart fail2ban
}

configure_firewall() {
    "${SCRIPT_DIR}/configure-firewall.sh"
}

install_systemd_units() {
    log "installing systemd units"
    local unit
    for unit in "${REPO_ROOT}"/systemd/aptplans-*.service "${REPO_ROOT}"/systemd/aptplans-*.timer; do
        [ -f "${unit}" ] || continue
        as_root cp "${unit}" /etc/systemd/system/
    done
    as_root cp "${HOST_CONFIG}/docker-cleanup.cron" /etc/cron.d/aptplans-docker-cleanup
    as_root cp "${HOST_CONFIG}/docker-cleanup.logrotate" /etc/logrotate.d/aptplans-docker-cleanup
    as_root chmod 644 /etc/cron.d/aptplans-docker-cleanup /etc/logrotate.d/aptplans-docker-cleanup
    as_root systemctl daemon-reload
    local timer
    for timer in /etc/systemd/system/aptplans-*.timer; do
        [ -f "${timer}" ] || continue
        local name
        name="$(basename "${timer}")"
        if [ "${name}" = "aptplans-pipeline.timer" ]; then
            as_root systemctl disable --now "${name}" || true
            continue
        fi
        as_root systemctl enable --now "${name}"
    done
    if [ -f /etc/systemd/system/aptplans-ollama-warmup.service ]; then
        as_root systemctl enable aptplans-ollama-warmup.service
    fi
    log "enabled timers:"
    as_root systemctl list-timers 'aptplans-*' --no-pager || true
}

write_env_file() {
    local env_file="/home/${APP_USER}/.env.production"
    as_root tee "${env_file}" >/dev/null <<EOF
# Written by scripts/host/bootstrap.sh - do not commit.
SITE_PATH=${SITE_DIR}
FILES_PATH=${FILES_DIR}
QUEUE_PATH=${QUEUE_DIR}
CATALOG_OVERLAY_PATH=${CATALOG_OVERLAY_DIR}
REPO_PATH=${REPO_DIR}
TLS_PATH=${TLS_DIR}
OLLAMA_PATH=${OLLAMA_DIR}
MODELS_PATH=${MODELS_DIR}
TEXT_PATH=${TEXT_DIR}
SEARCH_PATH=${SEARCH_DIR}
REJECT_PATH=${REJECT_DIR}
LOGS_PATH=${LOGS_DIR}
EGRESS_PATH=${EGRESS_DIR}
# EPYC 7351P: node 0 for site/worker/search/host, nodes 1-3 for Ollama.
SITE_CPUSET=0-3,16-19
WORKER_CPUSET=0-3,16-19
SEARCH_CPUSET=0-3,16-19
OLLAMA_CPUSET=4-15,20-31
APTPLANS_USER_AGENT=aptplans.org
EOF
    as_root chown "${APP_USER}:${APP_USER}" "${env_file}"
    as_root chmod 600 "${env_file}"
}

ensure_search_env() {
    # Written once. CD overwrites .env.secrets and must not rotate this key.
    local search_file="/home/${APP_USER}/.env.search"
    if [ -f "${search_file}" ]; then
        as_root chmod 600 "${search_file}"
        as_root chown "${APP_USER}:${APP_USER}" "${search_file}"
        return
    fi
    local key
    key="$(openssl rand -hex 24)"
    as_root tee "${search_file}" >/dev/null <<EOF
# Origin Meilisearch master key. Written once by bootstrap. Do not commit.
MEILI_MASTER_KEY=${key}
EOF
    as_root chown "${APP_USER}:${APP_USER}" "${search_file}"
    as_root chmod 600 "${search_file}"
}

ensure_secrets_file() {
    # CD writes PIA, intake, review, and search tokens here. Do not clobber an existing file.
    local secrets_file="/home/${APP_USER}/.env.secrets"
    if [ -f "${secrets_file}" ]; then
        as_root chmod 600 "${secrets_file}"
        as_root chown "${APP_USER}:${APP_USER}" "${secrets_file}"
        return
    fi
    as_root tee "${secrets_file}" >/dev/null <<EOF
# Written by GitHub Actions Deploy (PIA VPN + intake + review tokens). Do not commit.
EOF
    as_root chown "${APP_USER}:${APP_USER}" "${secrets_file}"
    as_root chmod 600 "${secrets_file}"
}

main() {
    require_debian_trixie
    install_base_packages
    install_docker
    ensure_app_user
    ensure_directories
    ensure_origin_tls
    configure_timezone
    configure_sysctl
    configure_sshd
    configure_unattended_upgrades
    configure_fail2ban
    configure_firewall
    install_systemd_units
    write_env_file
    ensure_secrets_file
    ensure_search_env
    log "bootstrap complete"
}

main "$@"
