from __future__ import annotations

from pathlib import Path

from pipeline.boot_jobs import enqueue_boot_jobs, enqueue_job, run_intake_once
from pipeline.queue import JobQueue, QueueJob
from pipeline.status import queue_dir_from_env


def test_enqueue_boot_jobs_dedupes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    (tmp_path / "overlay").mkdir()
    (tmp_path / "overlay" / "airports.jsonl").write_text('{"lid":"PDX"}\n', encoding="utf-8")
    (tmp_path / "overlay" / "grants.jsonl").write_text('{"airport_lid":"PDX"}\n', encoding="utf-8")
    monkeypatch.delenv("APTPLANS_REFRESH_AIRPORTS", raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    first = enqueue_boot_jobs(tmp_path / "queue")
    second = enqueue_boot_jobs(tmp_path / "queue")
    assert first == []
    assert second == []
    queue = JobQueue(queue_dir_from_env(tmp_path / "queue"))
    assert not queue.has_kind("site_build")
    assert not queue.has_kind("pipeline_snapshot")


def test_enqueue_boot_jobs_queues_stale_overlay(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    (tmp_path / "overlay").mkdir()
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")
    monkeypatch.setenv("APP_ENV", "production")
    enqueued = enqueue_boot_jobs(tmp_path / "queue")
    assert enqueued[0] == "overlay_refresh"
    assert set(enqueued).issubset({"overlay_refresh", "ollama_warm"})


def test_enqueue_job_unknown_kind(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    try:
        enqueue_job(tmp_path / "queue", "not_a_job")
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_intake_timer_only_enqueues(tmp_path: Path, monkeypatch) -> None:
    queue_dir = tmp_path / "queue"
    monkeypatch.setattr(
        "pipeline.run_once._intake_job_from_github",
        lambda: QueueJob(
            kind="fetch",
            document_id="plan",
            source_url="https://example.com/plan.pdf",
            airport_lid="PDX",
            issue_number=42,
        ),
    )
    assert run_intake_once(queue_dir=queue_dir) is True
    queue = JobQueue(queue_dir)
    assert queue.counts()["pending"] == 1
    assert queue.counts()["active"] == 0
    assert queue.counts()["done"] == 0
