"""Scheduler paths enqueue work; they do not run heavy jobs inline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from pipeline.boot_jobs import (
    BOOT_JOB_KINDS,
    enqueue_job,
    enqueue_monthly_refresh,
    enqueue_pipeline_snapshot,
    run_discovery,
    run_pipeline_snapshot,
)
from pipeline.queue import JobQueue, QueueJob
from pipeline.run_once import _schedule_after_job, process_next
from pipeline.worker import main as worker_main

ROOT = Path(__file__).resolve().parents[1]


def test_explore_enqueues_pipeline_snapshot_not_site_build(tmp_path: Path, monkeypatch) -> None:
    from tests.test_explore import HUB_WITH_LINKS

    html_path = tmp_path / "mulino.html"
    html_path.write_text(HUB_WITH_LINKS, encoding="utf-8")
    queue_dir = tmp_path / "queue"
    JobQueue(queue_dir).enqueue(
        QueueJob(
            kind="explore",
            document_id="4s9-site",
            source_url=html_path.resolve().as_uri(),
            airport_lid="4S9",
            state="OR",
        )
    )
    assert (
        process_next(
            queue_dir=queue_dir,
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
        )
        is True
    )
    queue = JobQueue(queue_dir)
    assert queue.has_kind("pipeline_snapshot")
    assert not queue.has_kind("site_build")


def test_fetch_enqueues_snapshot_and_site_build(tmp_path: Path, monkeypatch) -> None:
    from catalog import REFERENCE_FILES

    queue_dir = tmp_path / "queue"
    JobQueue(queue_dir).enqueue(
        QueueJob(
            kind="fetch",
            document_id="4s9-2008-inventory",
            source_url=REFERENCE_FILES.joinpath("4s9-2008-inventory.pdf").resolve().as_uri(),
            airport_lid="4S9",
        )
    )
    assert (
        process_next(
            queue_dir=queue_dir,
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
        )
        is True
    )
    queue = JobQueue(queue_dir)
    assert queue.has_kind("pipeline_snapshot")
    assert queue.has_kind("site_build")


def test_schedule_after_job_is_enqueue_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    snapshot = MagicMock(return_value=True)
    site = MagicMock(return_value=True)
    monkeypatch.setattr("pipeline.run_once.enqueue_pipeline_snapshot", snapshot)
    monkeypatch.setattr("pipeline.site_build.enqueue_site_build", site)

    _schedule_after_job(
        tmp_path / "queue",
        QueueJob(kind="fetch", document_id="x", source_url="https://x", airport_lid="4S9"),
        ROOT / "catalog",
    )
    snapshot.assert_called_once()
    site.assert_called_once()


def test_schedule_after_job_skips_boot_kinds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    snapshot = MagicMock()
    monkeypatch.setattr("pipeline.run_once.enqueue_pipeline_snapshot", snapshot)

    for kind in BOOT_JOB_KINDS + ("pipeline_snapshot", "site_build"):
        _schedule_after_job(
            tmp_path / "queue",
            QueueJob(kind=kind, document_id=None, source_url=None, airport_lid=None),
            ROOT / "catalog",
        )
    snapshot.assert_not_called()


def test_schedule_after_job_enqueues_snapshot_for_discovery(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    snapshot = MagicMock(return_value=True)
    monkeypatch.setattr("pipeline.run_once.enqueue_pipeline_snapshot", snapshot)

    _schedule_after_job(
        tmp_path / "queue",
        QueueJob(kind="discovery", document_id=None, source_url=None, airport_lid=None),
        ROOT / "catalog",
    )
    snapshot.assert_called_once()


def test_pull_discovery_dedupes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    queue_dir = tmp_path / "queue"
    overlay = tmp_path / "overlay"
    overlay.mkdir(parents=True)
    overlay.joinpath("airports.jsonl").write_text(
        '{"lid":"PDX","name":"Portland","city":"Portland","state":"OR"}\n',
        encoding="utf-8",
    )
    overlay.joinpath("grants.jsonl").write_text(
        '{"airport_lid":"PDX","level":"federal","obligated":1,"state":"OR"}\n',
        encoding="utf-8",
    )

    assert (
        process_next(
            queue_dir=queue_dir,
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
            pull_discovery=True,
        )
        is True
    )
    assert JobQueue(queue_dir).has_kind("discovery")
    from pipeline.boot_jobs import enqueue_discovery_if_ready

    assert enqueue_discovery_if_ready(queue_dir, overlay) is False


def test_worker_boot_only_enqueues(monkeypatch, tmp_path: Path) -> None:
    from pipeline import worker

    enqueued: list[str] = []
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    (tmp_path / "overlay").mkdir()
    (tmp_path / "overlay" / "airports.jsonl").write_text('{"lid":"PDX"}\n', encoding="utf-8")
    (tmp_path / "overlay" / "grants.jsonl").write_text('{"airport_lid":"PDX"}\n', encoding="utf-8")
    monkeypatch.delenv("APTPLANS_REFRESH_AIRPORTS", raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(worker, "enqueue_boot_jobs", lambda *_a, **_k: enqueued.append("boot") or ["pipeline_snapshot"])
    monkeypatch.setattr(worker, "run_loop", lambda: enqueued.append("loop"))
    worker_main()
    assert enqueued == ["boot", "loop"]


def test_pipeline_snapshot_job_writes_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    (tmp_path / "overlay").mkdir()
    assert run_pipeline_snapshot(tmp_path / "overlay", tmp_path / "queue", ROOT / "catalog") == "ok"
    assert (tmp_path / "overlay" / "pipeline.json").is_file()


def test_discovery_job_enqueues_explore(tmp_path: Path, monkeypatch) -> None:
    import json

    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    (tmp_path / "overlay").mkdir(parents=True)
    (tmp_path / "overlay" / "airports.jsonl").write_text(
        '{"lid":"4S9","name":"Mulino","city":"Mulino","state":"OR"}\n',
        encoding="utf-8",
    )
    (tmp_path / "overlay" / "grants.jsonl").write_text(
        '{"airport_lid":"4S9","level":"federal","obligated":1,"state":"OR"}\n',
        encoding="utf-8",
    )

    def fake_discover(overlay_dir, queue_dir, **kwargs):
        JobQueue(queue_dir).enqueue(
            QueueJob(
                kind="explore",
                document_id=None,
                source_url="https://example.com/hub",
                airport_lid="4S9",
                state="OR",
            )
        )
        return {"explore_jobs": 1, "fetch_jobs": 0, "airports": ["4S9"]}

    monkeypatch.setattr("pipeline.discover_overlay.discover_next_airports", fake_discover)
    monkeypatch.setattr("pipeline.pipeline_status.record_discovery", lambda *_a, **_k: None)

    status = run_discovery(tmp_path / "overlay", tmp_path / "queue")
    assert status == "ok"
    kinds = {
        json.loads(path.read_text(encoding="utf-8"))["kind"]
        for path in (tmp_path / "queue" / "pending").glob("*.json")
    }
    assert "explore" in kinds


def test_monthly_refresh_enqueues_overlay_when_faa_on(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    (tmp_path / "overlay").mkdir()
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")
    enqueued = enqueue_monthly_refresh(tmp_path / "queue")
    assert enqueued == ["overlay_refresh"]


def test_check_enqueue_flag(tmp_path: Path, monkeypatch) -> None:
    import sys

    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    monkeypatch.setattr(sys, "argv", ["check", "--enqueue"])
    from pipeline.check import main as check_main

    assert check_main() == 0
    assert JobQueue(tmp_path / "queue").has_kind("link_check")


def test_enqueue_pipeline_snapshot_dedupes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    assert enqueue_pipeline_snapshot(tmp_path / "queue") is True
    assert enqueue_pipeline_snapshot(tmp_path / "queue") is False
