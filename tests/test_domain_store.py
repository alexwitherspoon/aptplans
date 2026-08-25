from pathlib import Path
import json
import sqlite3

import pytest

from catalog.seed import seed_catalog_snapshot
from pipeline.domain_store import DomainStore, StaleGenerationError
from pipeline.queue import JobQueue

ROOT = Path(__file__).resolve().parents[1]


def test_domain_generations_are_immutable_snapshots(tmp_path: Path) -> None:
    store = DomainStore(tmp_path)
    first = store.commit(
        {("documents", "plan"): {"id": "plan", "review_status": "pending"}},
        reason="ingest plan",
    )
    second = store.patch(
        "documents",
        "plan",
        {"review_status": "published"},
        reason="publish plan",
    )
    assert first.generation_id != second.generation_id
    assert first.get("documents", "plan")["review_status"] == "pending"
    assert second.get("documents", "plan")["review_status"] == "published"
    assert store.snapshot(first.generation_id) == first
    assert store.path == JobQueue(tmp_path).path


def test_stale_generation_cannot_overwrite_newer_state(tmp_path: Path) -> None:
    store = DomainStore(tmp_path)
    first = store.snapshot()
    store.patch(
        "documents",
        "plan",
        {"id": "plan"},
        reason="add plan",
        expected_generation_id=first.generation_id,
    )
    with pytest.raises(StaleGenerationError):
        store.patch(
            "documents",
            "other",
            {"id": "other"},
            reason="stale write",
            expected_generation_id=first.generation_id,
        )


def test_replace_deletes_only_from_new_generation(tmp_path: Path) -> None:
    store = DomainStore(tmp_path)
    first = store.replace(
        "airports",
        [{"lid": "PDX"}, {"lid": "TTD"}],
        key_field="lid",
        reason="initial airports",
    )
    second = store.replace(
        "airports",
        [{"lid": "PDX", "name": "Portland"}],
        key_field="lid",
        reason="replace airports",
    )
    assert [row["lid"] for row in first.rows("airports")] == ["PDX", "TTD"]
    assert second.rows("airports") == [{"lid": "PDX", "name": "Portland"}]


def test_failed_generation_commit_leaves_current_pointer_unchanged(
    tmp_path: Path,
) -> None:
    store = DomainStore(tmp_path)
    first = store.snapshot()
    with pytest.raises(TypeError):
        store.commit(
            {("documents", "bad"): {"value": object()}},
            reason="invalid payload",
            expected_generation_id=first.generation_id,
        )
    assert store.current_generation_id() == first.generation_id


def test_export_is_generation_bound_interchange(tmp_path: Path) -> None:
    store = DomainStore(tmp_path / "queue")
    snapshot = store.commit(
        {
            ("documents", "plan"): {"id": "plan"},
            ("airports", "PDX"): {"lid": "PDX"},
        },
        reason="fixture",
    )
    destination = tmp_path / "export"
    exported = store.export_jsonl(destination)
    assert exported.generation_id == snapshot.generation_id
    assert json.loads((destination / "current" / "generation.json").read_text())[
        "generation_id"
    ] == snapshot.generation_id
    assert json.loads((destination / "current" / "documents.jsonl").read_text()) == {
        "id": "plan"
    }


def test_committed_generation_tables_reject_mutation(tmp_path: Path) -> None:
    store = DomainStore(tmp_path)
    snapshot = store.commit(
        {("documents", "plan"): {"id": "plan"}},
        reason="fixture",
    )
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM generation_entities WHERE generation_id=?",
                (snapshot.generation_id,),
            )


def test_catalog_snapshot_is_bound_to_domain_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APTPLANS_DEV_PREVIEW", "0")
    snapshot = DomainStore(tmp_path).commit(
        {
            ("airports", "PDX"): {
                "lid": "PDX",
                "name": "Portland International",
                "city": "Portland",
                "state": "OR",
            },
            ("documents", "pdx-plan"): {
                "id": "pdx-plan",
                "kind": "master_plan",
                "source_url": "https://example.com/pdx-plan.pdf",
                "completeness": "complete",
                "airport_lid": "PDX",
            },
        },
        reason="catalog fixture",
    )
    catalog_snapshot = seed_catalog_snapshot(ROOT / "catalog", snapshot)
    assert catalog_snapshot.generation_id == snapshot.generation_id
    assert catalog_snapshot.catalog.airports_by_lid["PDX"].state == "OR"
    assert catalog_snapshot.catalog.documents_by_id["pdx-plan"].airport_lid == "PDX"


def test_dataset_state_and_append_only_audits_share_generation(tmp_path: Path) -> None:
    store = DomainStore(tmp_path)
    snapshot = store.commit(
        {},
        reason="dataset ready",
        dataset_state={"airports": {"status": "ready", "rows": 2}},
    )
    assert snapshot.dataset_state["airports"]["status"] == "ready"
    assert store.append_audit(
        "outcomes",
        {"job_id": "job-1", "status": "accepted"},
        event_key="job:job-1:accepted",
        generation_id=snapshot.generation_id,
    )
    assert not store.append_audit(
        "outcomes",
        {"job_id": "job-1", "status": "accepted"},
        event_key="job:job-1:accepted",
    )
    assert store.audit_records("outcomes") == [
        {"job_id": "job-1", "status": "accepted"}
    ]


def test_grant_replace_uses_stable_fallback_and_rejects_duplicates(
    tmp_path: Path,
) -> None:
    store = DomainStore(tmp_path)
    grant = {
        "grant_number": None,
        "airport_lid": "PDX",
        "fiscal_year": 2026,
        "description": "Taxiway",
    }
    snapshot = store.replace(
        "grants",
        [grant],
        key_field="grant_number",
        reason="grant refresh",
    )
    assert snapshot.rows("grants") == [grant]
    with pytest.raises(ValueError, match="duplicate grants key"):
        store.replace(
            "grants",
            [grant, dict(grant)],
            key_field="grant_number",
            reason="invalid duplicate",
        )
