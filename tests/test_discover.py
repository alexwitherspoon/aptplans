from __future__ import annotations

from pathlib import Path

from pipeline.discover import seed_reference_fetches
from pipeline.queue import JobQueue

ROOT = Path(__file__).resolve().parents[1]


def test_seed_reference_fetches_queues_link_only_pdfs(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue")
    count = seed_reference_fetches(queue, ROOT / "catalog")
    assert count > 0
    assert len(list(queue.pending.glob("*.json"))) == count
    job = queue.claim()
    assert job is not None
    assert job.kind == "fetch"


def test_seed_reference_fetches_skips_on_production_overlay(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("APTPLANS_DEV_PREVIEW", raising=False)
    monkeypatch.delenv("APTPLANS_REFERENCE_SEED", raising=False)
    queue = JobQueue(tmp_path / "queue")
    assert seed_reference_fetches(queue, ROOT / "catalog") == 0
    assert list(queue.pending.glob("*.json")) == []
