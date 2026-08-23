from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import hashlib

import pytest

from catalog import REFERENCE_FILES
from catalog.seed import seed_catalog
from pipeline.lock import worker_lock
from pipeline.boot_jobs import MAINTENANCE_JOB_KINDS
from pipeline.queue import JobQueue, JobRetry, QueueJob
from pipeline.run_once import process_next, run_once

INVENTORY = REFERENCE_FILES / "4s9-2008-inventory.pdf"
ROOT = Path(__file__).resolve().parents[1]


def _drain(**kwargs) -> None:
    while process_next(pull_intake=False, **kwargs):
        pass


def test_run_once_empty_queue_is_success(tmp_path: Path) -> None:
    assert (
        run_once(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
        )
        == 0
    )


def test_process_next_idle_is_false(tmp_path: Path) -> None:
    assert (
        process_next(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
        )
        is False
    )


def test_run_once_preserves_fixture_and_writes_overlay(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue")
    queue.enqueue(
        QueueJob(
            kind="fetch",
            document_id="4s9-2008-inventory",
            source_url=INVENTORY.resolve().as_uri(),
            airport_lid="4S9",
            issue_number=None,
        )
    )
    files = tmp_path / "files"
    overlay = tmp_path / "overlay"
    _drain(
        queue_dir=tmp_path / "queue",
        files_dir=files,
        overlay_dir=overlay,
        catalog_root=ROOT / "catalog",
    )
    digest = hashlib.sha256(INVENTORY.read_bytes()).hexdigest()
    assert (files / f"{digest}.pdf").is_file()
    sidecar = tmp_path / "text" / f"{digest}.jsonl"
    assert sidecar.is_file()
    assert "Mulino" in sidecar.read_text(encoding="utf-8")
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    doc = catalog.document("4s9-2008-inventory")
    assert doc.completeness == "complete"
    assert doc.review_status == "pending"
    assert doc.content_sha256 == digest
    assert JobQueue(tmp_path / "queue").claim() is None


def test_run_once_admits_lid_not_in_catalog(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue")
    queue.enqueue(
        QueueJob(
            kind="fetch",
            document_id=None,
            source_url=INVENTORY.resolve().as_uri(),
            airport_lid="XYZ",
            state="OR",
            issue_number=None,
        )
    )
    overlay = tmp_path / "overlay"
    _drain(
        queue_dir=tmp_path / "queue",
        files_dir=tmp_path / "files",
        overlay_dir=overlay,
        catalog_root=ROOT / "catalog",
    )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    assert catalog.airports_by_lid["XYZ"].admitted is True
    docs = catalog.documents_for_airport("XYZ")
    assert docs
    assert docs[0].completeness == "complete"


def test_run_once_reuses_same_content_as_mirror(tmp_path: Path) -> None:
    first = tmp_path / "plan-2010.pdf"
    second = tmp_path / "copy.pdf"
    first.write_bytes(INVENTORY.read_bytes())
    second.write_bytes(INVENTORY.read_bytes())
    overlay = tmp_path / "overlay"
    files = tmp_path / "files"
    for source in (first, second):
        queue = JobQueue(tmp_path / "queue")
        queue.enqueue(
            QueueJob(
                kind="fetch",
                document_id=None,
                source_url=source.resolve().as_uri(),
                airport_lid="QQQ",
                suggested_kind="master_plan",
                issue_number=None,
            )
        )
        _drain(
            queue_dir=tmp_path / "queue",
            files_dir=files,
            overlay_dir=overlay,
            catalog_root=ROOT / "catalog",
        )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    docs = catalog.documents_for_airport("QQQ")
    assert len(docs) == 1
    assert second.resolve().as_uri() in docs[0].mirrors


def test_run_once_new_edition_supersedes_prior_work(tmp_path: Path) -> None:
    other = REFERENCE_FILES / "4s9-2008-alternatives.pdf"
    overlay = tmp_path / "overlay"
    files = tmp_path / "files"
    older = tmp_path / "master-plan-2010.pdf"
    newer = tmp_path / "master-plan-2024.pdf"
    older.write_bytes(INVENTORY.read_bytes())
    newer.write_bytes(other.read_bytes())
    for source in (older, newer):
        queue = JobQueue(tmp_path / "queue")
        queue.enqueue(
            QueueJob(
                kind="fetch",
                document_id=None,
                source_url=source.resolve().as_uri(),
                airport_lid="RRR",
                suggested_kind="master_plan",
                issue_number=None,
            )
        )
        _drain(
            queue_dir=tmp_path / "queue",
            files_dir=files,
            overlay_dir=overlay,
            catalog_root=ROOT / "catalog",
        )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    by_url = {doc.source_url: doc for doc in catalog.documents_for_airport("RRR")}
    assert len(by_url) == 2
    assert by_url[newer.resolve().as_uri()].supersedes == by_url[older.resolve().as_uri()].id


def test_run_once_records_change_when_hash_differs(tmp_path: Path) -> None:
    other = REFERENCE_FILES / "4s9-2008-alternatives.pdf"
    queue = JobQueue(tmp_path / "queue")
    overlay = tmp_path / "overlay"
    files = tmp_path / "files"
    for source in (INVENTORY, other):
        queue.enqueue(
            QueueJob(
                kind="fetch",
                document_id="4s9-2008-inventory",
                source_url=source.resolve().as_uri(),
                airport_lid="4S9",
                issue_number=None,
            )
        )
        _drain(
            queue_dir=tmp_path / "queue",
            files_dir=files,
            overlay_dir=overlay,
            catalog_root=ROOT / "catalog",
        )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    assert catalog.document("4s9-2008-inventory").content_sha256 == hashlib.sha256(
        other.read_bytes()
    ).hexdigest()
    assert catalog.changes
    assert catalog.changes[0].entity_id == "4s9-2008-inventory"
    assert catalog.changes[0].from_sha256 != catalog.changes[0].to_sha256
    assert "Text or drawings" in (catalog.changes[0].unofficial_note or "")
    assert catalog.document("4s9-2008-inventory").text_sha256
    assert catalog.document("4s9-2008-inventory").images_sha256


def test_process_next_does_not_refresh_airports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")

    def boom(*_args, **_kwargs):
        raise AssertionError("must not refresh FAA overlays during a document job")

    monkeypatch.setattr("pipeline.refresh_airports.maybe_refresh", boom)
    queue = JobQueue(tmp_path / "queue")
    queue.enqueue(
        QueueJob(
            kind="fetch",
            document_id="4s9-2008-inventory",
            source_url=INVENTORY.resolve().as_uri(),
            airport_lid="4S9",
            issue_number=None,
        )
    )
    assert (
        process_next(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
        )
        is True
    )


def test_process_next_rebuilds_after_unlock(tmp_path: Path, monkeypatch) -> None:
    order: list[str] = []
    real_lock = worker_lock

    @contextmanager
    def tracking_lock(queue_dir: Path):
        order.append("lock")
        with real_lock(queue_dir):
            yield
        order.append("unlock")

    def rebuild() -> None:
        order.append("rebuild")

    monkeypatch.setattr("pipeline.run_once.worker_lock", tracking_lock)
    monkeypatch.setattr("pipeline.site_build.enqueue_site_build", lambda *_args, **_kwargs: rebuild())
    queue = JobQueue(tmp_path / "queue")
    queue.enqueue(
        QueueJob(
            kind="fetch",
            document_id="4s9-2008-inventory",
            source_url=INVENTORY.resolve().as_uri(),
            airport_lid="4S9",
            issue_number=None,
        )
    )
    assert (
        process_next(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
        )
        is True
    )
    assert order == ["lock", "unlock", "lock", "unlock", "rebuild"]


def test_process_next_vet_rebuilds_after_unlock(tmp_path: Path, monkeypatch) -> None:
    order: list[str] = []
    real_lock = worker_lock

    @contextmanager
    def tracking_lock(queue_dir: Path):
        order.append("lock")
        with real_lock(queue_dir):
            yield
        order.append("unlock")

    def rebuild() -> None:
        order.append("rebuild")

    monkeypatch.setattr("pipeline.run_once.worker_lock", tracking_lock)
    monkeypatch.setattr("pipeline.site_build.enqueue_site_build", lambda *_args, **_kwargs: rebuild())
    monkeypatch.setattr("pipeline.run_once.process_vet", lambda *_args, **_kwargs: "auto_pass")
    queue = JobQueue(tmp_path / "queue")
    queue.enqueue(
        QueueJob(
            kind="vet",
            document_id="4s9-2008-inventory",
            source_url=INVENTORY.resolve().as_uri(),
            airport_lid="4S9",
            issue_number=None,
        )
    )
    assert (
        process_next(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
        )
        is True
    )
    assert order == ["lock", "unlock", "lock", "unlock", "rebuild"]


def test_process_next_retries_then_gives_up(tmp_path: Path, monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("overlay write failed")

    monkeypatch.setattr("pipeline.run_once.process_fetch", boom)
    queue = JobQueue(tmp_path / "queue")
    queue.enqueue(
        QueueJob(
            kind="fetch",
            document_id="4s9-2008-inventory",
            source_url=INVENTORY.resolve().as_uri(),
            airport_lid="4S9",
            issue_number=None,
        )
    )
    kwargs = dict(
        queue_dir=tmp_path / "queue",
        files_dir=tmp_path / "files",
        overlay_dir=tmp_path / "overlay",
        catalog_root=ROOT / "catalog",
    )
    with pytest.raises(JobRetry) as first:
        process_next(**kwargs)
    assert first.value.attempts == 1
    with pytest.raises(JobRetry) as second:
        process_next(**kwargs)
    assert second.value.attempts == 2
    assert process_next(**kwargs) is True
    assert list((tmp_path / "queue" / "active").glob("*.json")) == []
    assert list((tmp_path / "queue" / "done").glob("*.json"))


def test_process_next_skips_github_when_disk_has_work(tmp_path: Path, monkeypatch) -> None:
    def boom():
        raise AssertionError("must not list GitHub issues when pending/ has a job")

    monkeypatch.setattr("pipeline.run_once.github_from_env", boom)
    queue = JobQueue(tmp_path / "queue")
    queue.enqueue(
        QueueJob(
            kind="fetch",
            document_id="4s9-2008-inventory",
            source_url=INVENTORY.resolve().as_uri(),
            airport_lid="4S9",
            issue_number=None,
        )
    )
    assert (
        process_next(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
            pull_intake=True,
        )
        is True
    )


def test_process_next_intake_enqueues_when_idle(tmp_path: Path, monkeypatch) -> None:
    from pipeline.github import Issue

    body = f"""
### What should we do?
Add a missing document

### What kind of document?
Airport master plan

### Airport
4S9

### State
OR

### Official URL
{INVENTORY.resolve().as_uri()}

### Notes
fixture
"""

    class Client:
        def open_intake_issues(self):
            return [Issue(number=42, title="add", body=body, state="open")]

        def comment(self, number: int, body: str) -> None:
            return None

        def close(self, number: int) -> None:
            return None

    monkeypatch.setattr("pipeline.run_once.github_from_env", lambda: Client())
    assert (
        process_next(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
            pull_intake=True,
        )
        is True
    )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=tmp_path / "overlay")
    assert any(
        doc.completeness == "complete" and doc.airport_lid == "4S9"
        for doc in catalog.documents
    )
    _drain(
        queue_dir=tmp_path / "queue",
        files_dir=tmp_path / "files",
        overlay_dir=tmp_path / "overlay",
        catalog_root=ROOT / "catalog",
    )
    assert (
        process_next(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
            pull_intake=True,
        )
        is False
    )


def test_process_next_explore_html_preserves_page_and_queues_pdfs(tmp_path: Path) -> None:
    from tests.test_explore import AMP, HUB_WITH_LINKS

    html_path = tmp_path / "mulino.html"
    html_path.write_text(HUB_WITH_LINKS, encoding="utf-8")
    queue = JobQueue(tmp_path / "queue")
    queue.enqueue(
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
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
        )
        is True
    )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=tmp_path / "overlay")
    hub = catalog.document("4s9-site")
    assert hub.kind == "other"
    assert hub.completeness == "complete"
    assert hub.review_status == "pending"
    assert hub.inferred_media() == "html"
    assert (tmp_path / "files" / f"{hub.content_sha256}.html").is_file()
    pending = JobQueue(tmp_path / "queue")
    child = pending.claim()
    assert child is not None
    fetch_urls = []
    vet_ids = []
    while child is not None:
        if child.kind in MAINTENANCE_JOB_KINDS:
            pending.complete(child)
            child = pending.claim()
            continue
        if child.kind == "fetch":
            fetch_urls.append(child.source_url)
            assert child.found_on == html_path.resolve().as_uri()
        elif child.kind == "vet":
            vet_ids.append(child.document_id)
        else:
            assert child.kind == "explore"
        pending.complete(child)
        child = pending.claim()
    assert AMP in fetch_urls
    assert "4s9-site" in vet_ids


def test_process_fetch_strips_html_api_keys(tmp_path: Path) -> None:
    fake_maps = "".join(("AIza", "Sy", "TESTKEYNOTREAL", "FORUNITTESTS"))
    html_path = tmp_path / "hub.html"
    html_path.write_text(
        f"<html><body>master plan {fake_maps}</body></html>",
        encoding="utf-8",
    )
    queue = JobQueue(tmp_path / "queue")
    queue.enqueue(
        QueueJob(
            kind="fetch",
            document_id=None,
            source_url=html_path.resolve().as_uri(),
            airport_lid="4S9",
            state="OR",
        )
    )
    assert (
        process_next(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
        )
        is True
    )
    stored = next((tmp_path / "files").glob("*.html"))
    assert "AIzaSy" not in stored.read_text(encoding="utf-8")


def test_process_next_pull_discovery_enqueues_job(tmp_path: Path, monkeypatch) -> None:
    from pipeline.queue import JobQueue

    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
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
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=tmp_path / "overlay",
            catalog_root=ROOT / "catalog",
            pull_discovery=True,
        )
        is True
    )
    queue = JobQueue(tmp_path / "queue")
    assert queue.has_kind("discovery")
    assert not list((tmp_path / "queue" / "done").glob("*.json"))
