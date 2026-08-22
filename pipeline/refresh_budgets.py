"""Enrich state budget overlays with rubric line_kind classification."""

from __future__ import annotations

import logging
import os
import time

from catalog.store import load_budgets_overlay, write_budgets_overlay
from pipeline.budget_classify import enrich_budget
from pipeline.ollama import llm_calls_enabled
from pipeline.refresh import PAUSE_SECONDS, overlay_dir_from_env

log = logging.getLogger("aptplans.budgets")


def enrich_budgets_overlay(
    overlay_dir,
    *,
    sleep=time.sleep,
    pause_seconds: float = PAUSE_SECONDS,
) -> int:
    budgets = load_budgets_overlay(overlay_dir)
    if not budgets:
        return 0
    if not llm_calls_enabled():
        log.info("budget line classification skipped; LLM disabled")
        return 0
    try:
        from pipeline.ollama import generate
    except Exception:
        log.exception("Ollama unavailable for budget line classification")
        return 0
    enriched = []
    for budget in budgets:
        enriched.append(enrich_budget(budget, generate_fn=generate))
        if pause_seconds:
            sleep(pause_seconds)
    write_budgets_overlay(overlay_dir, enriched)
    log.info("enriched %s state budgets with line_kind", len(enriched))
    return len(enriched)


def maybe_enrich_budgets(overlay_dir=None) -> int | None:
    overlay = overlay_dir_from_env(overlay_dir)
    path = overlay / "budgets.jsonl"
    if not path.is_file():
        return None
    return enrich_budgets_overlay(overlay)
