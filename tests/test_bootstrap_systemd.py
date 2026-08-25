from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "systemd"
BOOTSTRAP = ROOT / "scripts" / "host" / "bootstrap.sh"
REMOTE_DEPLOY = ROOT / "scripts" / "host" / "remote-deploy.sh"
DOMAIN_CUTOVER = ROOT / "scripts" / "host" / "domain-cutover.sh"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
COMPOSE_PROD = ROOT / "docker" / "docker-compose.prod.yml"

DISABLED_TIMERS = frozenset({"aptplans-pipeline.timer"})


def _timer_names() -> list[str]:
    return sorted(path.name for path in SYSTEMD.glob("aptplans-*.timer"))


def test_timer_inventory_matches_repo() -> None:
    assert set(_timer_names()) == {
        "aptplans-airports.timer",
        "aptplans-intake.timer",
        "aptplans-links.timer",
        "aptplans-overview-refresh.timer",
        "aptplans-pipeline-snapshot.timer",
        "aptplans-pipeline.timer",
        "aptplans-reboot.timer",
        "aptplans-search-sync.timer",
        "aptplans-search.timer",
        "aptplans-site-build.timer",
    }


def test_every_timer_is_enabled_by_bootstrap_except_pipeline() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    for name in _timer_names():
        if name in DISABLED_TIMERS:
            assert "aptplans-pipeline.timer" in text
            assert "disable --now" in text
            continue
        assert name.replace(".timer", "") in text or "aptplans-*.timer" in text


def test_bootstrap_installs_units_with_glob() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert 'systemd/aptplans-*.service' in text
    assert 'systemd/aptplans-*.timer' in text
    assert "systemctl list-timers 'aptplans-*'" in text


def test_remote_deploy_runs_bootstrap_before_compose() -> None:
    text = REMOTE_DEPLOY.read_text(encoding="utf-8")
    bootstrap_at = text.index("bootstrap.sh")
    compose_at = text.index("docker compose")
    assert bootstrap_at < compose_at
    assert "Applying host bootstrap" in text


def test_cd_invokes_remote_deploy_only() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert "sudo /opt/aptplans/scripts/host/remote-deploy.sh" in text
    assert "scripts/host/bootstrap.sh" not in text


def test_deploy_health_does_not_require_an_active_site_release() -> None:
    endpoint = "https://127.0.0.1/review/v1/health"
    assert endpoint in REMOTE_DEPLOY.read_text(encoding="utf-8")
    assert endpoint in COMPOSE_PROD.read_text(encoding="utf-8")
    assert "/review/v1/health" in DEPLOY.read_text(encoding="utf-8")


def test_domain_cutover_is_explicit_and_offline() -> None:
    workflow = DEPLOY.read_text(encoding="utf-8")
    script = DOMAIN_CUTOVER.read_text(encoding="utf-8")
    assert "domain_cutover" in workflow
    assert "domain-cutover.sh" in workflow
    assert "stop worker review site" in script
    assert "pre-domain-${STAMP}" in script
    assert "--confirm-preproduction-cutover" in script
    assert "run_site_build" in script
