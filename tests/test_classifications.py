from __future__ import annotations

from pathlib import Path

from pipeline.classifications import classification_stats, load_classifications, record_classification, utc_now


def test_record_and_load_classifications(tmp_path: Path) -> None:
    record_classification(
        tmp_path,
        evaluation="grant_spend",
        input_id="3-41-0048-094-2024",
        category="maintenance",
        classifier="llm",
        reason="reconstruct taxiway",
    )
    rows = load_classifications(tmp_path)
    assert len(rows) == 1
    assert rows[0]["evaluation"] == "grant_spend"
    stats = classification_stats(tmp_path)
    assert stats["total"] == 1
    assert stats["by_evaluation"]["grant_spend"]["maintenance"] == 1


def test_classification_stats_month_total(tmp_path: Path, monkeypatch) -> None:
    prefix = utc_now()[:7]
    record_classification(
        tmp_path,
        evaluation="grant_spend",
        input_id="a",
        category="maintenance",
        classifier="llm",
    )
    stats = classification_stats(tmp_path)
    assert stats["month_total"] == 1
    assert prefix in utc_now()
