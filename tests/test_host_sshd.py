from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSHD = ROOT / "config" / "host" / "sshd.conf"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"


def test_sshd_allows_only_aptplans() -> None:
    text = SSHD.read_text(encoding="utf-8")
    assert "PermitRootLogin no" in text
    assert "AllowUsers aptplans" in text
    assert "prohibit-password" not in text
    assert "PasswordAuthentication no" in text


def test_cd_requires_aptplans_user() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert 'DEPLOY_USER}" != "aptplans"' in text
    assert "sudo /opt/aptplans/scripts/host/remote-deploy.sh" in text
    assert "/opt/aptplans/scripts/host/remote-deploy.sh" in text
    assert 'if [ "$(id -u)" -eq 0 ]' not in text
    assert "MEILI_MASTER_KEY" not in text
    assert ".env.search" not in text
    assert "secrets.APTPLANS_REVIEW_TOKEN" in text
    assert "secrets.APTPLANS_SEARCH_KEY" in text
    assert "secrets.APTPLANS_GEMINI_KEY" in text
    assert 'echo "review API token: set"' in text
    assert 'echo "Brave search key: set"' in text
    assert 'echo "Gemini search key: set"' in text
    assert "review API token: ${" not in text
    assert "Brave search key: ${" not in text
    assert "Gemini search key: ${" not in text
