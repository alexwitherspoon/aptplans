from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from catalog.models import Document
from catalog.seed import seed_catalog
from pipeline.check import (
    ProbeResult,
    check_document,
    due_documents,
    overlay_for_probe,
    probe_url,
    run_check_pass,
    same_resource,
    wayback_capture,
)
from pipeline.queue import JobQueue, QueueJob
from pipeline.run_once import run_once


def _doc(**kwargs) -> Document:
    payload = {
        "id": "pdx-plan",
        "kind": "master_plan",
        "source_url": "https://example.com/plan.pdf",
        "completeness": "complete",
        "airport_lid": "PDX",
        "source_status": "live",
        "content_sha256": "abc",
        "preserved_url": "/files/abc.pdf",
    }
    payload.update(kwargs)
    return Document.from_dict(payload)


def test_same_resource_ignores_trailing_slash() -> None:
    assert same_resource("https://Example.com/a/b", "https://example.com/a/b/") is True
    assert same_resource("https://example.com/a", "https://example.com/b") is False


def test_probe_marks_404_dead() -> None:
    result = probe_url("https://example.com/gone.pdf", meta_fn=lambda url, method="HEAD": (404, url))
    assert result.status == "dead"
    assert result.http_status == 404


def test_probe_head_fallback_to_get() -> None:
    calls = []

    def meta(url: str, method: str = "HEAD") -> tuple[int, str]:
        calls.append(method)
        if method == "HEAD":
            return 405, url
        return 200, url

    result = probe_url("https://example.com/plan.pdf", meta_fn=meta)
    assert result.status == "live"
    assert calls == ["HEAD", "GET"]


def test_probe_5xx_is_error_not_dead() -> None:
    result = probe_url("https://example.com/plan.pdf", meta_fn=lambda url, method="HEAD": (503, url))
    assert result.status == "error"


def test_probe_redirect_is_moved() -> None:
    result = probe_url(
        "https://example.com/old.pdf",
        meta_fn=lambda url, method="HEAD": (200, "https://example.com/new.pdf"),
    )
    assert result.status == "moved"
    assert result.final_url == "https://example.com/new.pdf"


def test_dead_complete_record_becomes_preserved_only() -> None:
    document = _doc()
    updates = overlay_for_probe(
        document,
        ProbeResult("dead", http_status=404, final_url=document.source_url),
        "2026-08-19T12:00:00Z",
    )
    assert updates["source_status"] == "dead"
    assert updates["completeness"] == "preserved_only"


def test_dead_link_only_becomes_missing() -> None:
    document = _doc(completeness="link_only", content_sha256=None, preserved_url=None)
    updates = overlay_for_probe(
        document,
        ProbeResult("dead", http_status=410),
        "2026-08-19T12:00:00Z",
    )
    assert updates["completeness"] == "missing"


def test_live_again_restores_complete() -> None:
    document = _doc(source_status="dead", completeness="preserved_only")
    updates = overlay_for_probe(document, ProbeResult("live", http_status=200), "2026-08-19T12:00:00Z")
    assert updates["source_status"] == "live"
    assert updates["completeness"] == "complete"


def test_due_skips_recent_live_and_rechecks_old_dead() -> None:
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    recent = _doc(source_retrieved_at="2026-08-18T00:00:00Z")
    stale = _doc(id="old", source_retrieved_at="2026-08-01T00:00:00Z")
    dead_fresh = _doc(
        id="dead-new",
        source_status="dead",
        source_retrieved_at="2026-08-10T00:00:00Z",
    )
    dead_stale = _doc(
        id="dead-old",
        source_status="dead",
        source_retrieved_at="2026-07-01T00:00:00Z",
    )
    file_url = _doc(id="local", source_url="file:///tmp/plan.pdf")
    due = {doc.id for doc in due_documents([recent, stale, dead_fresh, dead_stale, file_url], now)}
    assert due == {"old", "dead-old"}


def test_check_dead_uses_live_mirror(tmp_path: Path) -> None:
    document = _doc(mirrors=["https://mirror.example/plan.pdf"])

    def probe(url: str):
        if "mirror" in url:
            return ProbeResult("live", http_status=200, final_url=url)
        return ProbeResult("dead", http_status=404, final_url=url)

    outcome = check_document(document, probe_fn=probe, wayback_fn=lambda url: None)
    assert outcome.status == "dead"
    assert outcome.fetch_url == "https://mirror.example/plan.pdf"


def test_check_dead_without_copy_uses_wayback() -> None:
    document = _doc(completeness="link_only", content_sha256=None, preserved_url=None, mirrors=[])
    ia = "https://web.archive.org/web/20200101000000id_/https://example.com/plan.pdf"
    outcome = check_document(
        document,
        probe_fn=lambda url: ProbeResult("dead", http_status=404),
        wayback_fn=lambda url: ia,
    )
    assert outcome.fetch_url == ia


def test_wayback_capture_parses_cdx() -> None:
    payload = b'[["timestamp","original","statuscode"],["20200101000000","https://example.com/plan.pdf","200"]]'

    def fetch(url: str, timeout: int = 20):
        assert "cdx" in url
        return payload, 200

    assert wayback_capture("https://example.com/plan.pdf", fetch_fn=fetch) == (
        "https://web.archive.org/web/20200101000000id_/https://example.com/plan.pdf"
    )


def test_run_check_pass_writes_overlay_and_queues_fetch(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    overlay = tmp_path / "overlay"
    queue_dir = tmp_path / "queue"
    catalog = seed_catalog(root / "catalog")
    first = due_documents(catalog.documents)[0]

    def probe(url: str):
        if url == first.source_url:
            return ProbeResult("dead", http_status=404, final_url=url)
        return ProbeResult("error", reason="skip")

    count = run_check_pass(
        overlay_dir=overlay,
        catalog_root=root / "catalog",
        queue_dir=queue_dir,
        limit=1,
        probe_fn=probe,
        wayback_fn=lambda url: "https://web.archive.org/web/1id_/https://example.com/x.pdf",
        sleep=lambda _s: None,
        pause_seconds=0,
    )
    assert count == 1
    updated = seed_catalog(root / "catalog", overlay_dir=overlay).document(first.id)
    assert updated.source_status == "dead"
    job = JobQueue(queue_dir).claim()
    assert job is not None
    assert job.kind == "fetch"
    assert job.document_id == first.id


def test_run_once_processes_check_job(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    overlay = tmp_path / "overlay"
    queue = JobQueue(tmp_path / "queue")
    catalog = seed_catalog(root / "catalog")
    sample = next(doc for doc in catalog.documents if doc.id == "pdx-2045-existing-conditions")
    queue.enqueue(
        QueueJob(
            kind="check",
            document_id=sample.id,
            source_url=sample.source_url,
            airport_lid=sample.airport_lid,
        )
    )

    from pipeline import check as check_mod

    def fake_probe(url: str):
        return ProbeResult("dead", http_status=404, final_url=url)

    original = check_mod.probe_url
    check_mod.probe_url = fake_probe
    try:
        assert (
            run_once(
                queue_dir=tmp_path / "queue",
                files_dir=tmp_path / "files",
                overlay_dir=overlay,
                catalog_root=root / "catalog",
            )
            == 0
        )
    finally:
        check_mod.probe_url = original
    updated = seed_catalog(root / "catalog", overlay_dir=overlay).document(sample.id)
    assert updated.source_status == "dead"
