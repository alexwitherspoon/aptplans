from pathlib import Path
import json

import pytest

from pipeline.domain_cutover import import_overlays
from pipeline.domain_store import DomainStore


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_cutover_imports_one_generation_and_audit_streams(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    _write(
        overlay / "airports.jsonl",
        [{"lid": "PDX", "name": "Portland", "city": "Portland", "state": "OR"}],
    )
    _write(
        overlay / "documents.jsonl",
        [
            {
                "id": "plan",
                "kind": "master_plan",
                "source_url": "https://example.com/plan.pdf",
                "completeness": "complete",
            }
        ],
    )
    _write(overlay / "outcomes.jsonl", [{"job_id": "job-1"}])
    (overlay / "datasets.json").write_text(
        json.dumps({"datasets": {"airports": {"status": "ready"}}}),
        encoding="utf-8",
    )
    result = import_overlays(
        overlay,
        tmp_path / "ledger",
        confirmed_preproduction=True,
    )
    assert result["entities"]["airports"] == 1
    store = DomainStore(tmp_path / "ledger")
    snapshot = store.snapshot(result["generation_id"])
    assert snapshot.get("documents", "plan")["kind"] == "master_plan"
    assert snapshot.dataset_state["airports"]["status"] == "ready"
    assert store.audit_records("outcomes") == [{"job_id": "job-1"}]
    repeated = import_overlays(
        overlay,
        tmp_path / "ledger",
        confirmed_preproduction=True,
    )
    assert repeated["generation_id"] == result["generation_id"]
    assert store.audit_records("outcomes") == [{"job_id": "job-1"}]


def test_cutover_rejects_duplicate_entity_keys_before_commit(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    _write(overlay / "airports.jsonl", [{"lid": "PDX"}, {"lid": "PDX"}])
    with pytest.raises(ValueError, match="duplicate key PDX"):
        import_overlays(
            overlay,
            tmp_path / "ledger",
            confirmed_preproduction=True,
        )
    assert DomainStore(tmp_path / "ledger").current_generation_id() is None


def test_cutover_requires_explicit_confirmation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="confirm-preproduction-cutover"):
        import_overlays(
            tmp_path / "overlay",
            tmp_path / "ledger",
            confirmed_preproduction=False,
        )


def test_cutover_can_initialize_one_state_only(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    _write(
        overlay / "airports.jsonl",
        [
            {"lid": "PDX", "state": "OR"},
            {"lid": "SEA", "state": "WA"},
        ],
    )
    _write(
        overlay / "documents.jsonl",
        [
            {"id": "pdx-plan", "airport_lid": "PDX"},
            {"id": "sea-plan", "airport_lid": "SEA"},
        ],
    )
    _write(
        overlay / "grants.jsonl",
        [
            {"grant_number": "OR-1", "airport_lid": "PDX"},
            {"grant_number": "WA-1", "airport_lid": "SEA"},
        ],
    )
    _write(
        overlay / "outcomes.jsonl",
        [
            {"job_id": "or-job", "state": "OR"},
            {"job_id": "wa-job", "state": "WA"},
        ],
    )
    result = import_overlays(
        overlay,
        tmp_path / "ledger",
        confirmed_preproduction=True,
        state_scope="or",
    )
    assert result["state_scope"] == "OR"
    assert result["entities"]["airports"] == 1
    assert result["entities"]["documents"] == 1
    assert result["entities"]["grants"] == 1
    snapshot = DomainStore(tmp_path / "ledger").snapshot(
        result["generation_id"]
    )
    assert snapshot.get("airports", "PDX") is not None
    assert snapshot.get("airports", "SEA") is None
    assert DomainStore(tmp_path / "ledger").audit_records("outcomes") == [
        {"job_id": "or-job", "state": "OR"}
    ]
