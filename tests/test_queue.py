from __future__ import annotations

import json
from pathlib import Path

from pipeline.queue import JobQueue, JobRetry, QueueJob


def _job(document_id: str, lid: str) -> QueueJob:
    return QueueJob(
        kind="fetch",
        document_id=document_id,
        source_url=f"https://example.com/{document_id}.pdf",
        airport_lid=lid,
        issue_number=None,
    )


def test_queue_is_fifo_and_completes_one_at_a_time(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    first = queue.enqueue(_job("a", "PDX"))
    queue.enqueue(_job("b", "TTD"))
    got = queue.claim()
    assert got is not None
    assert got.document_id == "a"
    assert got.id == first.id
    queue.complete(got)
    remaining = queue.claim()
    assert remaining is not None
    assert remaining.document_id == "b"
    queue.complete(remaining)
    assert queue.claim() is None


def test_empty_queue_claims_none(tmp_path: Path) -> None:
    assert JobQueue(tmp_path).claim() is None


def test_unfinished_claim_retries_on_next_queue(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    queue.enqueue(_job("a", "PDX"))
    claimed = queue.claim()
    assert claimed is not None
    retry = JobQueue(tmp_path)
    got = retry.claim()
    assert got is not None
    assert got.document_id == "a"
    assert got.attempts == 2


def test_claim_increments_attempts_and_persists(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    queue.enqueue(_job("a", "PDX"))
    first = queue.claim()
    assert first is not None
    assert first.attempts == 1
    active = list((tmp_path / "active").glob("*.json"))
    assert len(active) == 1
    stored = json.loads(active[0].read_text(encoding="utf-8"))
    assert stored["attempts"] == 1


def test_job_retry_backoff_caps_at_one_hour() -> None:
    assert JobRetry(1).delay_seconds() == 60
    assert JobRetry(2).delay_seconds() == 120
    assert JobRetry(3).delay_seconds() == 240
    assert JobRetry(10).delay_seconds() == 3600


def test_claim_one_airport_at_a_time(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    queue.enqueue(_job("a", "PDX"))
    queue.enqueue(_job("b", "TTD"))
    queue.enqueue(_job("c", "PDX"))

    first = queue.claim(airport_limit=1)
    assert first is not None
    assert first.document_id == "a"
    queue.complete(first)

    second = queue.claim(airport_limit=1)
    assert second is not None
    assert second.document_id == "c"
    queue.complete(second)

    third = queue.claim(airport_limit=1)
    assert third is not None
    assert third.document_id == "b"
    queue.complete(third)


def test_claim_two_airports_when_limit_two(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    queue.enqueue(_job("a", "PDX"))
    queue.enqueue(_job("b", "TTD"))

    first = queue.claim(airport_limit=2)
    assert first is not None
    assert first.document_id == "a"

    second = JobQueue(tmp_path).claim(airport_limit=2)
    assert second is not None
    assert second.document_id == "b"


def test_has_issue_looks_in_done(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    job = _job("a", "PDX")
    job.issue_number = 17
    queue.enqueue(job)
    claimed = queue.claim()
    assert claimed is not None
    queue.complete(claimed)
    assert JobQueue(tmp_path).has_issue(17) is True
    assert JobQueue(tmp_path).has_issue(99) is False
