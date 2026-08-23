from __future__ import annotations

from pathlib import Path

from pipeline.boot_jobs import enqueue_job, enqueue_pipeline_snapshot
from pipeline.queue import JobQueue


def test_timer_enqueue_flags(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    assert enqueue_pipeline_snapshot(tmp_path / "queue") is True
    assert JobQueue(tmp_path / "queue").has_kind("pipeline_snapshot")
    assert enqueue_job(tmp_path / "queue", "overview_refresh") is True
    assert enqueue_job(tmp_path / "queue", "search_sync") is True
    assert enqueue_job(tmp_path / "queue", "site_build") is True
