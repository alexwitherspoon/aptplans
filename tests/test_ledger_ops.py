from pathlib import Path
import json

import pytest
from pypdf import PdfWriter

from pipeline.extraction_store import ExtractionStore
from pipeline.ledger_ops import backup, integrity, reset, restore
from pipeline.queue import ControlQueue, JobQueue, QueueJob


def _job() -> QueueJob:
    return QueueJob(
        kind="fetch",
        document_id="plan",
        source_url="https://example.com/plan.pdf",
        airport_lid="PDX",
    )


def test_backup_restore_recreates_operable_ledgers(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    JobQueue(root).enqueue(_job())
    ControlQueue(root).enqueue(
        QueueJob(
            kind="review",
            document_id="plan",
            source_url="https://example.com/plan.pdf",
            airport_lid="PDX",
        )
    )
    destination = tmp_path / "backup"
    assert backup(root, destination) == {"jobs": "ok", "control": "ok"}
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["sha256"]) == {"jobs.sqlite3", "control.sqlite3"}

    restored = tmp_path / "restored"
    assert restore(restored, destination, confirmed_offline=True) == {
        "jobs": "ok",
        "control": "ok",
    }
    assert JobQueue(restored).counts()["pending"] == 1
    assert ControlQueue(restored).counts()["pending"] == 1
    claimed = JobQueue(restored).claim()
    assert claimed is not None
    JobQueue(restored).complete(claimed)
    assert JobQueue(restored).counts()["done"] == 1


def test_restore_and_reset_require_explicit_confirmation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="confirm-offline"):
        restore(tmp_path / "queue", tmp_path / "backup", confirmed_offline=False)
    with pytest.raises(ValueError, match="confirm-preproduction-reset"):
        reset(tmp_path / "queue", confirmed_preproduction=False)


def test_reset_creates_clean_versioned_ledgers(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    JobQueue(root).enqueue(_job())
    assert reset(root, confirmed_preproduction=True) == {
        "jobs": "ok",
        "control": "ok",
    }
    assert JobQueue(root).counts()["pending"] == 0
    assert integrity(root) == {"jobs": "ok", "control": "ok"}


def test_restore_rejects_changed_backup_bytes(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    JobQueue(root).enqueue(_job())
    destination = tmp_path / "backup"
    backup(root, destination)
    with (destination / "jobs.sqlite3").open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        restore(
            tmp_path / "restored",
            destination,
            confirmed_offline=True,
        )


def test_backup_restore_preserves_indexed_extractions(tmp_path: Path) -> None:
    source_pdf = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source_pdf.open("wb") as handle:
        writer.write(handle)

    root = tmp_path / "queue"
    source_extractions = tmp_path / "source-extractions"
    manifest = ExtractionStore(root, source_extractions).extract_pdf(
        source_pdf
    )
    destination = tmp_path / "backup"
    backup(
        root,
        destination,
        extraction_root=source_extractions,
    )

    restored = tmp_path / "restored"
    restored_extractions = tmp_path / "restored-extractions"
    restore(
        restored,
        destination,
        confirmed_offline=True,
        extraction_root=restored_extractions,
    )
    indexed = ExtractionStore(
        restored, restored_extractions
    ).get(manifest.manifest_key)
    assert indexed is not None
    assert indexed.manifest_sha256 == manifest.manifest_sha256
