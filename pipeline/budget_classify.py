"""Classify state budget rows when unstructured ingest is added."""

from __future__ import annotations

import logging
from dataclasses import replace

from catalog.models import Budget, BudgetLine
from pipeline.queries import classify_budget_line

log = logging.getLogger("aptplans.budget_classify")


def enrich_budget_line(
    line: BudgetLine,
    *,
    state: str = "",
    generate_fn=None,
) -> BudgetLine:
    rule_kind = line.line_kind or line.group or "program"
    if generate_fn is None:
        return replace(line, line_kind=rule_kind)
    try:
        scored = classify_budget_line(
            category=line.category,
            note=line.note or "",
            state=state,
            generate_fn=generate_fn,
            rule_kind=rule_kind,
        )
        return replace(line, line_kind=scored.get("line_kind") or rule_kind)
    except Exception:
        log.exception("budget line LLM failed category=%s", line.category)
        return replace(line, line_kind=rule_kind)


def enrich_budget(
    budget: Budget,
    *,
    generate_fn=None,
) -> Budget:
    lines = [
        enrich_budget_line(line, state=budget.state, generate_fn=generate_fn) for line in budget.lines
    ]
    return replace(budget, lines=lines)
