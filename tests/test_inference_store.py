from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest
from pypdf import PdfWriter

from pipeline.extraction_store import ExtractionStore
from pipeline.inference_store import InferenceKey, InferenceStore
from pipeline.queue import JobQueue


EMPTY_TEXT_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb924"
    "27ae41e4649b934ca495991b7852b855"
)


def _store_and_key(tmp_path: Path) -> tuple[InferenceStore, InferenceKey]:
    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source.open("wb") as handle:
        writer.write(handle)
    ledger = tmp_path / "ledger"
    extractions = tmp_path / "extractions"
    manifest = ExtractionStore(
        ledger, extractions
    ).extract_pdf(source)
    return (
        InferenceStore(ledger, extractions),
        InferenceKey(
            content_sha256=manifest.content_sha256,
            extraction_manifest_key=manifest.manifest_key,
            task_type="airport_budget_facts",
            task_version="1",
            lane="complex_text",
            model_digest=f"sha256:{'a' * 64}",
            options={"temperature": 0, "schema_version": 1},
        ),
    )


def test_checkpoint_resumes_and_completion_clears_it(
    tmp_path: Path,
) -> None:
    store, key = _store_and_key(tmp_path)
    assert store.checkpoint(key, cursor=1, state={"pages": [1]})
    assert store.load_checkpoint(key)["state"] == {"pages": [1]}
    assert store.checkpoint(key, cursor=2, state={"pages": [1, 2]})
    with pytest.raises(
        sqlite3.IntegrityError, match="cursor cannot regress"
    ):
        store.checkpoint(key, cursor=1, state={"pages": [1]})

    completed = store.complete(
        key,
        result={"fund": "airport", "total": 404300},
        evidence=[{"page": 1, "text_sha256": EMPTY_TEXT_SHA256}],
        quality={"schema_valid": True},
    )
    assert completed.result_sha256
    assert store.load_checkpoint(key) is None
    assert store.get(key) == completed
    assert store.checkpoint(key, cursor=3, state={}) is False


def test_inference_identity_changes_with_model_or_options(
    tmp_path: Path,
) -> None:
    _store, key = _store_and_key(tmp_path)
    changed_model = replace(
        key,
        model_digest=f"sha256:{'b' * 64}",
    )
    changed_options = replace(
        key,
        options={"temperature": 0, "schema_version": 2},
    )
    assert changed_model.task_key != key.task_key
    assert changed_options.task_key != key.task_key


def test_inference_results_are_idempotent_and_immutable(
    tmp_path: Path,
) -> None:
    store, key = _store_and_key(tmp_path)
    arguments = {
        "result": {"answer": "supported"},
        "evidence": [
            {"page": 1, "text_sha256": EMPTY_TEXT_SHA256}
        ],
        "quality": {"schema_valid": True},
    }
    first = store.complete(key, **arguments)
    assert store.complete(key, **arguments) == first
    with pytest.raises(ValueError, match="result conflict"):
        store.complete(
            key,
            result={"answer": "different"},
            evidence=arguments["evidence"],
            quality=arguments["quality"],
        )

    with sqlite3.connect(JobQueue(tmp_path / "ledger").path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE inference_results SET task_version='2'"
            )


def test_inference_result_requires_evidence(tmp_path: Path) -> None:
    store, key = _store_and_key(tmp_path)
    with pytest.raises(ValueError, match="requires cited evidence"):
        store.complete(
            key,
            result={"answer": "unsupported"},
            evidence=[],
        )
    with pytest.raises(ValueError, match="does not match extraction"):
        store.complete(
            key,
            result={"answer": "unsupported"},
            evidence=[{"page": 1, "text_sha256": "c" * 64}],
        )


def test_checkpoint_requires_existing_extraction_and_bounded_state(
    tmp_path: Path,
) -> None:
    store, key = _store_and_key(tmp_path)
    missing = replace(
        key,
        extraction_manifest_key="f" * 64,
    )
    with pytest.raises(ValueError, match="does not match extraction"):
        store.checkpoint(missing, cursor=0, state={})
    with pytest.raises(ValueError, match="exceeds size limit"):
        store.checkpoint(
            key,
            cursor=0,
            state={"payload": "x" * (64 * 1024)},
        )


def test_inference_read_verifies_complete_envelope(tmp_path: Path) -> None:
    store, key = _store_and_key(tmp_path)
    store.complete(
        key,
        result={"answer": "supported"},
        evidence=[
            {"page": 1, "text_sha256": EMPTY_TEXT_SHA256}
        ],
    )
    with sqlite3.connect(JobQueue(tmp_path / "ledger").path) as connection:
        connection.execute("DROP TRIGGER inference_results_no_update")
        connection.execute(
            """
            UPDATE inference_results
            SET evidence_json='[{"page":1,"text_sha256":"changed"}]'
            """
        )
    with pytest.raises(ValueError, match="corrupt inference result"):
        store.get(key)


def test_model_identity_requires_immutable_digest(tmp_path: Path) -> None:
    _store, key = _store_and_key(tmp_path)
    with pytest.raises(ValueError, match="SHA-256 digest"):
        replace(key, model_digest="latest")
