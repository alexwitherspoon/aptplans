from __future__ import annotations

import hashlib
from pathlib import Path

from catalog import REFERENCE_FILES
from catalog.seed import seed_catalog
from pipeline.queue import JobQueue, QueueJob
from pipeline.run_once import run_once

INVENTORY = REFERENCE_FILES / "4s9-2008-inventory.pdf"
ROOT = Path(__file__).resolve().parents[1]


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
    assert (
        run_once(
            queue_dir=tmp_path / "queue",
            files_dir=files,
            overlay_dir=overlay,
            catalog_root=ROOT / "catalog",
        )
        == 0
    )
    digest = hashlib.sha256(INVENTORY.read_bytes()).hexdigest()
    assert (files / f"{digest}.pdf").is_file()
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    doc = catalog.document("4s9-2008-inventory")
    assert doc.completeness == "complete"
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
    assert (
        run_once(
            queue_dir=tmp_path / "queue",
            files_dir=tmp_path / "files",
            overlay_dir=overlay,
            catalog_root=ROOT / "catalog",
        )
        == 0
    )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    assert catalog.airports_by_lid["XYZ"].admitted is True
    docs = catalog.documents_for_airport("XYZ")
    assert docs
    assert docs[0].completeness == "complete"


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
        assert (
            run_once(
                queue_dir=tmp_path / "queue",
                files_dir=files,
                overlay_dir=overlay,
                catalog_root=ROOT / "catalog",
            )
            == 0
        )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    assert catalog.document("4s9-2008-inventory").content_sha256 == hashlib.sha256(
        other.read_bytes()
    ).hexdigest()
    assert catalog.changes
    assert catalog.changes[0].entity_id == "4s9-2008-inventory"
    assert catalog.changes[0].from_sha256 != catalog.changes[0].to_sha256
