from pathlib import Path

from catalog.models import Airport, Document
from catalog.seed import seed_catalog
from catalog.store import write_airports_overlay, write_overlay_update
from pipeline.classifications import load_classifications, record_classification
from pipeline.datasets import dataset_should_refresh, load_catalog, mark_dataset_ready
from pipeline.outcomes import load_outcomes, record_outcome
from pipeline.queue import ControlQueue, JobQueue
from pipeline.run_once import _apply_review_updates, _rollback_failed_promotion

ROOT = Path(__file__).resolve().parents[1]


def test_domain_mode_replaces_operational_jsonl_readers_and_writers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue = tmp_path / "queue"
    overlay = tmp_path / "overlay"
    monkeypatch.setenv("APTPLANS_DOMAIN_STORE", "1")
    monkeypatch.setenv("APTPLANS_QUEUE", str(queue))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(overlay))
    monkeypatch.setenv("APTPLANS_DEV_PREVIEW", "0")

    write_airports_overlay(
        overlay,
        [Airport(lid="PDX", name="Portland", city="Portland", state="OR")],
    )
    write_overlay_update(
        overlay,
        "plan",
        {
            "kind": "master_plan",
            "source_url": "https://example.com/plan.pdf",
            "completeness": "complete",
            "airport_lid": "PDX",
        },
    )
    record_classification(
        overlay,
        evaluation="grant_spend",
        input_id="grant-1",
        category="planning",
        classifier="rule",
    )
    record_outcome(
        overlay,
        {"job_id": "job-1", "job_status": "preserved"},
        strict=True,
    )
    control = tmp_path / "control"
    monkeypatch.setenv("APTPLANS_CONTROL_QUEUE", str(control))
    monkeypatch.setenv("APTPLANS_CONTROL_WRITER", "1")
    record_outcome(
        overlay,
        {"id": "human-1", "gold": {"publish": True}},
        strict=True,
    )
    mark_dataset_ready(overlay, "airports", job_kind="overlay_refresh")

    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    assert catalog.airports_by_lid["PDX"].state == "OR"
    assert catalog.documents_by_id["plan"].completeness == "complete"
    assert len(load_classifications(overlay)) == 1
    assert len(load_outcomes(overlay)) == 2
    assert ControlQueue(queue).root == control
    assert ControlQueue(queue).counts()["pending"] == 0
    assert len(ControlQueue(queue).audit_records("outcomes")) == 1
    assert load_catalog(overlay)["datasets"]["airports"]["status"] == "ready"
    assert not dataset_should_refresh(overlay, "airports")
    assert not overlay.exists()
    monkeypatch.setenv("APTPLANS_JOB_LEDGER_READ_ONLY", "1")
    assert seed_catalog(ROOT / "catalog").airports_by_lid["PDX"].state == "OR"
    assert JobQueue(queue).counts()["pending"] == 0


def test_generation_publication_keeps_committed_review_state_on_release_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue = tmp_path / "queue"
    overlay = tmp_path / "overlay"
    monkeypatch.setenv("APTPLANS_DOMAIN_STORE", "1")
    monkeypatch.setenv("APTPLANS_QUEUE", str(queue))
    document = Document(
        id="plan",
        kind="master_plan",
        source_url="https://example.com/plan.pdf",
        completeness="complete",
        review_status="pending",
    )
    write_overlay_update(overlay, document.id, document.to_dict())
    _apply_review_updates(
        document,
        {"review_status": "published"},
        overlay_dir=overlay,
        catalog_root=ROOT / "catalog",
        files_dir=tmp_path / "files",
    )
    _rollback_failed_promotion(
        document,
        type("Job", (), {"document_id": "plan"})(),
        tmp_path / "files",
        overlay,
        ROOT / "catalog",
    )
    current = seed_catalog(ROOT / "catalog", overlay).documents_by_id["plan"]
    assert current.review_status == "published"
