from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from pathlib import Path

from pipeline.queue import ControlQueue, JobQueue, JobRetry, QueueJob


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


def test_unfinished_claim_waits_for_lease_then_recovers(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    queue.enqueue(_job("a", "PDX"))
    claimed = queue.claim()
    assert claimed is not None
    retry = JobQueue(tmp_path)
    assert retry.claim() is None
    with sqlite3.connect(retry.path) as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at='1970-01-01T00:00:00Z' WHERE id=?",
            (claimed.id,),
        )
    assert retry.recover_expired_leases() == 1
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
    active = queue.jobs(state="active")
    assert len(active) == 1
    assert active[0].attempts == 1
    assert queue.path.is_file()


def test_job_retry_backoff_caps_at_one_hour() -> None:
    assert JobRetry(1).delay_seconds() == 60
    assert JobRetry(2).delay_seconds() == 120
    assert JobRetry(3).delay_seconds() == 240
    assert JobRetry(10).delay_seconds() == 3600


def test_review_request_round_trips_through_queue(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    queue.enqueue(
        QueueJob(
            kind="review",
            document_id="plan",
            source_url="https://example.com/plan.pdf",
            airport_lid="PDX",
            requested_review_status="published",
            expected_content_sha256="a" * 64,
            requested_by="operator",
            request_reason="source verified",
        )
    )
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.requested_review_status == "published"
    assert claimed.expected_content_sha256 == "a" * 64
    assert claimed.requested_by == "operator"
    assert claimed.request_reason == "source verified"


def test_publication_maintenance_preempts_bulk_airport_work(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    queue.enqueue(_job("bulk", "PDX"))
    queue.enqueue(
        QueueJob(
            kind="site_build",
            document_id=None,
            source_url=None,
            airport_lid=None,
        )
    )
    queue.enqueue(
        QueueJob(
            kind="pipeline_snapshot",
            document_id=None,
            source_url=None,
            airport_lid=None,
        )
    )

    first = queue.claim()
    assert first is not None
    assert first.kind == "pipeline_snapshot"
    queue.complete(first)
    second = queue.claim()
    assert second is not None
    assert second.kind == "site_build"


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


def test_queue_uses_wal_and_deduplicates_open_work(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    first = _job("a", "PDX")
    first.dedupe_key = "source:a"
    duplicate = _job("duplicate", "PDX")
    duplicate.dedupe_key = "source:a"
    assert queue.enqueue(first).id == first.id
    assert queue.enqueue(duplicate).id == first.id
    assert queue.counts()["pending"] == 1
    with sqlite3.connect(queue.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_parent_continuation_waits_for_committed_success(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    parent = queue.enqueue(_job("parent", "PDX"))
    child = _job("child", "TTD")
    child.priority = 1000
    child.parent_job_id = parent.id
    queue.enqueue(child)
    claimed_parent = queue.claim(airport_limit=2)
    assert claimed_parent is not None
    assert claimed_parent.id == parent.id
    assert JobQueue(tmp_path).claim(airport_limit=2) is None
    queue.complete(claimed_parent)
    claimed_child = queue.claim(airport_limit=2)
    assert claimed_child is not None
    assert claimed_child.id == child.id


def test_retry_time_heartbeat_progress_and_dead_letter(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    queued = queue.enqueue(_job("a", "PDX"))
    claimed = queue.claim()
    assert claimed is not None
    queue.heartbeat(claimed, progress={"page": 4})
    assert queue.jobs(state="active")[0].progress == {"page": 4}
    queue.retry(claimed, delay_seconds=3600, error="temporary")
    assert queue.claim() is None
    queue.reschedule_now(queued.id)
    retried = queue.claim()
    assert retried is not None
    assert retried.attempts == 2
    queue.dead_letter(retried, error="terminal")
    assert queue.counts()["dead"] == 1
    with sqlite3.connect(queue.path) as connection:
        outcomes = connection.execute(
            "SELECT outcome FROM attempts WHERE job_id=? ORDER BY attempt_number",
            (queued.id,),
        ).fetchall()
    assert outcomes == [("retry",), ("dead",)]


def test_control_inbox_is_separate_and_worker_ingest_is_idempotent(
    tmp_path: Path,
) -> None:
    controls = ControlQueue(tmp_path)
    command = QueueJob(
        kind="review",
        document_id="plan",
        source_url="https://example.com/plan.pdf",
        airport_lid="PDX",
        requested_review_status="published",
    )
    controls.enqueue(command)
    jobs = JobQueue(tmp_path)
    assert jobs.counts()["pending"] == 0
    assert controls.counts()["pending"] == 1
    assert jobs.ingest_controls(controls) == 1
    assert jobs.ingest_controls(controls) == 0
    assert jobs.counts()["pending"] == 1
    assert controls.counts()["accepted"] == 1


def test_control_import_recovers_after_job_insert_before_audit_accept(
    tmp_path: Path,
) -> None:
    controls = ControlQueue(tmp_path)
    command = QueueJob(
        kind="review",
        document_id="plan",
        source_url="https://example.com/plan.pdf",
        airport_lid="PDX",
    )
    controls.enqueue(command)
    jobs = JobQueue(tmp_path)
    jobs.enqueue(command)
    assert jobs.ingest_controls(controls) == 1
    assert jobs.counts()["pending"] == 1
    assert controls.counts()["accepted"] == 1


def test_concurrent_claim_is_not_duplicated(tmp_path: Path) -> None:
    JobQueue(tmp_path).enqueue(_job("only", "PDX"))

    def claim() -> str | None:
        job = JobQueue(tmp_path).claim(airport_limit=4)
        return job.id if job else None

    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = list(executor.map(lambda _index: claim(), range(4)))
    assert len([job_id for job_id in claimed if job_id is not None]) == 1
    assert JobQueue(tmp_path).counts()["active"] == 1


def test_same_airport_is_serialized_even_with_multiple_slots(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    queue.enqueue(_job("a", "PDX"))
    queue.enqueue(_job("b", "PDX"))
    first = queue.claim(airport_limit=2)
    assert first is not None
    assert JobQueue(tmp_path).claim(airport_limit=2) is None
    queue.complete(first)
    assert queue.claim(airport_limit=2) is not None
