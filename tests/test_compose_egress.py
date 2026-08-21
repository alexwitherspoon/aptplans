from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker" / "docker-compose.yml"
COMPOSE_PROD = ROOT / "docker" / "docker-compose.prod.yml"
COMPOSE_LOCAL = ROOT / "docker" / "docker-compose.local.yml"


def test_prod_worker_uses_internal_http_egress() -> None:
    prod = COMPOSE_PROD.read_text(encoding="utf-8")
    assert "egress:" in prod
    assert "APTPLANS_FETCH_PROXY=http://egress:8888" in prod
    assert "condition: service_healthy" in prod


def test_egress_is_internal_only() -> None:
    prod = COMPOSE_PROD.read_text(encoding="utf-8")
    assert "8888:8888" not in prod
    assert "HTTPPROXY_LISTENING_ADDRESS: :8888" in prod
    assert "qmcgaw/gluetun" in prod


def test_local_worker_does_not_require_egress() -> None:
    local = COMPOSE_LOCAL.read_text(encoding="utf-8")
    base = COMPOSE.read_text(encoding="utf-8")
    assert "APTPLANS_FETCH_PROXY=${APTPLANS_FETCH_PROXY:-}" in base
    assert "egress:" not in local
    assert "APTPLANS_FETCH_PROXY=http://egress:8888" not in local


def test_prod_egress_uses_pia_openvpn_env() -> None:
    prod = COMPOSE_PROD.read_text(encoding="utf-8")
    assert "VPN_SERVICE_PROVIDER: private internet access" in prod
    assert "PIA_OPENVPN_USER" in prod
    assert "PIA_OPENVPN_PASSWORD" in prod
    assert "OPENVPN_PROTOCOL: tcp" in prod
    assert "HEALTH_TARGET_ADDRESS: 1.1.1.1:443" in prod
    assert ":/gluetun" in prod
