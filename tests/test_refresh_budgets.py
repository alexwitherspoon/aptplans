from __future__ import annotations

import json
import os
from pathlib import Path

from catalog.models import Budget, BudgetLine
from catalog.store import write_budgets_overlay
from pipeline.refresh_budgets import enrich_budgets_overlay, maybe_enrich_budgets


def test_maybe_enrich_budgets_skips_missing_overlay(tmp_path: Path) -> None:
    assert maybe_enrich_budgets(tmp_path) is None


def test_enrich_budgets_overlay_skips_without_llm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_LLM", raising=False)
    write_budgets_overlay(
        tmp_path,
        [
            Budget(
                id="or-2025",
                state="OR",
                source_url="https://example.test/or-budget",
                fiscal_year=2025,
                lines=[BudgetLine(category="Operations", note="State aviation fund")],
            )
        ],
    )
    assert enrich_budgets_overlay(tmp_path) == 0


def test_enrich_budgets_overlay_with_mock_llm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_LLM", "1")

    def generate(_prompt: str) -> str:
        return '{"line_kind":"program","reason":"state fund line"}'

    import pipeline.ollama as ollama_mod

    monkeypatch.setattr(ollama_mod, "generate", generate)
    write_budgets_overlay(
        tmp_path,
        [
            Budget(
                id="or-2025",
                state="OR",
                source_url="https://example.test/or-budget",
                fiscal_year=2025,
                lines=[BudgetLine(category="Operations", note="State aviation fund")],
            )
        ],
    )
    count = enrich_budgets_overlay(tmp_path, pause_seconds=0)
    assert count == 1
    rows = [json.loads(line) for line in (tmp_path / "budgets.jsonl").read_text().splitlines()]
    assert rows[0]["lines"][0]["line_kind"] == "program"
