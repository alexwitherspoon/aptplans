from __future__ import annotations

from pathlib import Path

from pipeline.queue import JobQueue, QueueJob


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
    queue.complete()
    remaining = queue.claim()
    assert remaining is not None
    assert remaining.document_id == "b"
    queue.complete()
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
