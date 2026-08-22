from __future__ import annotations

import json
from pathlib import Path

from pipeline.discover_overlay import discovery_limit
from pipeline.outcomes import record_outcome
from pipeline.pipeline_status import (
    build_public_snapshot,
    coverage_banner,
    coverage_stage,
    load_status,
    record_discovery,
    record_job,
)
from pipeline.queue import JobQueue, QueueJob
from pipeline.worker import discovery_idle_seconds

ROOT = Path(__file__).resolve().parents[1]


def test_record_outcome_never_raises_on_bad_scored(tmp_path: Path) -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird"

    row = record_outcome(
        tmp_path,
        {
            "url": "https://example.com/plan.pdf",
            "job_status": "preserved",
            "scored": {"kind": Weird()},
        },
    )
    assert row["url"].startswith("https://")
    path = tmp_path / "outcomes.jsonl"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["scored"]["kind"] == "weird"


def test_pipeline_status_and_public_snapshot(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    queue_dir = tmp_path / "queue"
    overlay.mkdir()
    (overlay / "airports.jsonl").write_text(
        '{"lid":"ZZ9","name":"Test Field","city":"Test","state":"OR","website":""}\n',
        encoding="utf-8",
    )
    job = QueueJob(
        kind="explore",
        document_id=None,
        source_url="https://example.com/hub",
        airport_lid="ZZ9",
        state="OR",
    )
    record_job(overlay, job, "preserved")
    record_discovery(overlay, ["ZZ9"])
    queue = JobQueue(queue_dir)
    queue.enqueue(
        QueueJob(
            kind="fetch",
            document_id=None,
            source_url="https://example.com/plan.pdf",
            airport_lid="ZZ9",
            state="OR",
        )
    )
    snapshot = build_public_snapshot(overlay, queue_dir, catalog_root=ROOT / "catalog")
    assert snapshot["queue"]["pending"] == 1
    assert snapshot["discovery"]["scoped_airports"] >= 1
    assert (overlay / "pipeline.json").is_file()
    rows = load_status(overlay)
    assert rows["ZZ9"]["explored_at"]
    stage = coverage_stage("ZZ9", overlay_dir=overlay, catalog_root=ROOT / "catalog", status_rows=rows)
    assert stage in {"explored", "searched", "snapshot_pending"}
    banner = coverage_banner(stage, rows["ZZ9"])
    assert banner


def test_discovery_defaults_tuned() -> None:
    assert discovery_limit() == 5
    assert discovery_idle_seconds() == 86400.0


def test_review_client_sets_user_agent() -> None:
    from pipeline.review_client import USER_AGENT, review_headers

    headers = review_headers("token")
    assert headers["User-Agent"] == USER_AGENT
    assert headers["User-Agent"] == "aptplans.org"
