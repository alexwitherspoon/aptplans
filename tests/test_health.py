from __future__ import annotations

from pathlib import Path

from pipeline.health import system_health


def _seed_overlay(overlay: Path) -> None:
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "airports.jsonl").write_text(
        '{"lid":"PDX","name":"Portland","city":"Portland","state":"OR"}\n',
        encoding="utf-8",
    )
    (overlay / "grants.jsonl").write_text(
        '{"airport_lid":"PDX","level":"federal","obligated":1,"state":"OR"}\n',
        encoding="utf-8",
    )


def test_system_health_reports_datasets_and_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    overlay = tmp_path / "overlay"
    _seed_overlay(overlay)
    payload = system_health(overlay, queue_dir=tmp_path / "queue")
    assert "datasets" in payload
    assert payload["datasets"]["airports"]["status"] in {"ready", "stale"}
    assert "summary" in payload
    assert "services" in payload
    assert "pipeline" in payload
    assert payload["summary"]["discovery_ready"] is True
    assert payload["overlay"]["airports"]["n"] == 1
    assert payload["queue"]["pending"] == 0


def test_system_health_not_ok_without_airports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    payload = system_health(overlay, queue_dir=tmp_path / "queue")
    assert payload["ok"] is False
    assert payload["summary"]["discovery_ready"] is False
    assert payload["summary"]["blocking"]
