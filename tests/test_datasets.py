from __future__ import annotations

from pathlib import Path

from pipeline.datasets import (
    STATUS_BUILDING,
    STATUS_MISSING,
    STATUS_READY,
    catalog_path,
    dataset_usable,
    mark_dataset_building,
    mark_dataset_ready,
    reconcile_catalog,
    requirements_met,
)
from pipeline.queue import JobQueue, QueueJob


def _write_airports(overlay: Path) -> None:
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "airports.jsonl").write_text(
        '{"lid":"PDX","name":"Portland","city":"Portland","state":"OR"}\n',
        encoding="utf-8",
    )


def _write_grants(overlay: Path) -> None:
    (overlay / "grants.jsonl").write_text(
        '{"airport_lid":"PDX","level":"federal","obligated":100,"state":"OR"}\n',
        encoding="utf-8",
    )


def test_mark_ready_writes_catalog(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    _write_airports(overlay)
    record = mark_dataset_ready(overlay, "airports", job_kind="overlay_refresh")
    assert record["status"] == STATUS_READY
    assert record["rows"] == 1
    assert catalog_path(overlay).is_file()


def test_reconcile_infers_missing_dataset(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    catalog = reconcile_catalog(overlay)
    assert catalog["datasets"]["airports"]["status"] == STATUS_MISSING


def test_requirements_met_for_discovery(tmp_path: Path, monkeypatch) -> None:
    overlay = tmp_path / "overlay"
    _write_airports(overlay)
    _write_grants(overlay)
    reconcile_catalog(overlay)
    ok, _ = requirements_met("discovery", overlay)
    assert ok


def test_requirements_met_blocks_while_building(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    queue_dir = tmp_path / "queue"
    _write_airports(overlay)
    _write_grants(overlay)
    mark_dataset_building(overlay, "grants", job_kind="overlay_refresh")
    JobQueue(queue_dir).enqueue(
        QueueJob(kind="overlay_refresh", document_id=None, source_url=None, airport_lid=None)
    )
    ok, reason = requirements_met("discovery", overlay, JobQueue(queue_dir))
    assert not ok
    assert "building" in reason or "overlay_refresh" in reason


def test_dataset_usable_allows_stale(tmp_path: Path, monkeypatch) -> None:
    overlay = tmp_path / "overlay"
    _write_airports(overlay)
    mark_dataset_ready(overlay, "airports", job_kind="overlay_refresh")
    monkeypatch.setattr("pipeline.datasets.should_refresh", lambda _path: True)
    catalog = reconcile_catalog(overlay)
    record = catalog["datasets"]["airports"]
    assert record["status"] == "stale"
    ok, _ = dataset_usable(overlay, "airports", allow_stale=True)
    assert ok
