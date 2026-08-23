from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "systemd"
BOOTSTRAP = ROOT / "scripts" / "host" / "bootstrap.sh"
REMOTE_DEPLOY = ROOT / "scripts" / "host" / "remote-deploy.sh"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"

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
