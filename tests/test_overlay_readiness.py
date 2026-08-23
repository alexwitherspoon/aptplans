from __future__ import annotations

from pathlib import Path

from pipeline.boot_jobs import (
    enqueue_boot_jobs,
    enqueue_discovery_if_ready,
    enqueue_post_overlay_refresh,
    run_discovery,
    run_grant_spend,
)
from pipeline.datasets import reconcile_catalog
from pipeline.overlay_readiness import discovery_ready, grant_spend_ready
from pipeline.queue import JobQueue, QueueJob


def _write_overlay(overlay: Path) -> None:
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "airports.jsonl").write_text(
        '{"lid":"PDX","name":"Portland","city":"Portland","state":"OR"}\n',
        encoding="utf-8",
    )
    (overlay / "grants.jsonl").write_text(
        '{"airport_lid":"PDX","level":"federal","obligated":100,"state":"OR"}\n',
        encoding="utf-8",
    )
    reconcile_catalog(overlay)


def test_discovery_ready_requires_airports(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "grants.jsonl").write_text('{"airport_lid":"PDX"}\n', encoding="utf-8")
    ready, reason = discovery_ready(overlay)
    assert not ready
    assert reason == "airports: missing"


def test_discovery_ready_requires_grants_when_funded_first(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    _write_overlay(overlay)
    (overlay / "grants.jsonl").unlink()
    reconcile_catalog(overlay)
    ready, reason = discovery_ready(overlay)
    assert not ready
    assert reason == "grants: missing"


def test_discovery_ready_waits_for_overlay_refresh(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    queue_dir = tmp_path / "queue"
    _write_overlay(overlay)
    JobQueue(queue_dir).enqueue(
        QueueJob(kind="overlay_refresh", document_id=None, source_url=None, airport_lid=None)
    )
    ready, reason = discovery_ready(overlay, JobQueue(queue_dir))
    assert not ready
    assert reason == "overlay_refresh in flight"


def test_run_discovery_deferred_without_airports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    (tmp_path / "overlay").mkdir()
    assert run_discovery(tmp_path / "overlay", tmp_path / "queue") == "deferred"


def test_enqueue_post_overlay_refresh_queues_discovery(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    _write_overlay(tmp_path / "overlay")
    monkeypatch.delenv("APTPLANS_REFRESH_AIRPORTS", raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    enqueued = enqueue_post_overlay_refresh(tmp_path / "queue")
    assert "discovery" in enqueued
    assert JobQueue(tmp_path / "queue").has_kind("discovery")


def test_enqueue_discovery_if_ready_skips_when_not_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    (tmp_path / "overlay").mkdir()
    assert enqueue_discovery_if_ready(tmp_path / "queue", tmp_path / "overlay") is False
    assert not JobQueue(tmp_path / "queue").has_kind("discovery")


def test_boot_defers_grant_spend_when_overlay_refresh_queued(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    (tmp_path / "overlay").mkdir()
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")
    monkeypatch.setenv("APP_ENV", "production")
    enqueued = enqueue_boot_jobs(tmp_path / "queue")
    assert "overlay_refresh" in enqueued
    assert "grant_spend" not in enqueued
    assert "budget_enrich" not in enqueued


def test_grant_spend_deferred_while_overlay_refresh_queued(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    _write_overlay(tmp_path / "overlay")
    monkeypatch.setenv("APP_ENV", "production")
    JobQueue(tmp_path / "queue").enqueue(
        QueueJob(kind="overlay_refresh", document_id=None, source_url=None, airport_lid=None)
    )
    ready, reason = grant_spend_ready(tmp_path / "overlay", JobQueue(tmp_path / "queue"))
    assert not ready
    assert reason == "overlay_refresh in flight"
    assert run_grant_spend(tmp_path / "overlay", tmp_path / "queue") == "deferred"
