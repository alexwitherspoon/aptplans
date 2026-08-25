import pytest

from pipeline import oregon_benchmark


def test_manifest_covers_every_committed_oregon_pdf_and_blocks_later_claims() -> None:
    manifest = oregon_benchmark._load_json(oregon_benchmark.MANIFEST_PATH)
    oregon_benchmark._validate_manifest(manifest)
    schema = oregon_benchmark._load_json(
        oregon_benchmark.ROOT
        / "catalog"
        / "oregon_benchmark_manifest.schema.json"
    )
    assert set(schema["required"]) == set(manifest)
    cases = oregon_benchmark._load_json(
        oregon_benchmark.REFERENCES / "cases.json"
    )
    oregon_document_ids = {
        document["id"]
        for case in cases["cases"]
        if case.get("state") == "OR"
        for document in case.get("documents") or []
    }
    embedded = oregon_benchmark._load_json(
        oregon_benchmark.REFERENCES / "cases.json"
    )["embedded"]
    expected = {
        row["document_id"]
        for row in embedded
        if row["document_id"] in oregon_document_ids
    }
    assert {row["document_id"] for row in manifest["artifacts"]} == expected
    assert len(manifest["artifacts"]) == 8
    assert manifest["claims"]["oregon_complete"] is False
    assert all(
        manifest["claims"][name] == "blocked"
        for name in (
            "milestone_4_oregon_vertical_proof",
            "milestone_5_statewide_expansion",
            "milestone_6_completion_contract",
        )
    )


def test_oregon_substrate_benchmark_round_trips_frozen_core() -> None:
    result = oregon_benchmark.run()
    assert result["status"] == "core_smoke_passed"
    assert result["benchmark_id"] == "oregon-substrate-v1"
    assert result["artifact_gate"]["status"] == "passed"
    assert {
        row["id"]
        for row in result["artifact_gate"]["artifacts"]
        if row["extracted"]
    } == {
        "4s9-inventory-born-digital",
        "4s2-alp-drawing",
        "ttd-integrated-master-plan",
    }
    assert result["funding_gate"]["state_budget_total"] == 45874157
    assert result["funding_gate"]["consolidated_budget_total"] == 45874157
    assert result["funding_gate"]["pdx_grant_count"] == 8
    assert result["domain_release_gate"]["status"] == "passed"
    assert result["domain_release_gate"]["repeat_clean_runs"] == 2
    assert {
        row["airports"] for row in result["domain_release_gate"]["runs"]
    } == {4}
    assert {
        row["documents"] for row in result["domain_release_gate"]["runs"]
    } == {8}
    assert {
        row["ledger_integrity"] for row in result["domain_release_gate"]["runs"]
    } == {"ok"}
    assert result["claims"]["oregon_complete"] is False
    assert (
        result["claims"]["milestone_2_clean_cutover_rerun"]
        == "core_smoke_only"
    )
    assert result["incomplete_modalities"] == [
        "scanned_pdf_ocr",
        "official_budget_table",
        "official_grant_spreadsheet",
    ]


def test_complete_corpus_gate_refuses_known_modality_gaps(monkeypatch) -> None:
    monkeypatch.setattr(
        oregon_benchmark,
        "_reference_rows",
        lambda _manifest: ([], [], [], []),
    )
    monkeypatch.setattr(
        oregon_benchmark,
        "_artifact_gate",
        lambda _manifest, full: {"status": "passed"},
    )
    monkeypatch.setattr(
        oregon_benchmark,
        "_funding_gate",
        lambda _manifest, _grants, _budgets: {"status": "passed"},
    )
    monkeypatch.setattr(
        oregon_benchmark,
        "_domain_release_gate",
        lambda *_args, **_kwargs: {"status": "passed"},
    )
    with pytest.raises(RuntimeError, match="corpus is incomplete"):
        oregon_benchmark.run(require_complete_corpus=True)
