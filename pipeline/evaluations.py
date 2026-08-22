"""Registry of rubric-driven classification tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

CLASSIFIERS = frozenset({"rules", "llm", "human"})


@dataclass(frozen=True)
class EvaluationSpec:
    name: str
    labels: frozenset[str]
    gold_path: str
    description: str


GRANT_SPEND = EvaluationSpec(
    name="grant_spend",
    labels=frozenset({"maintenance", "growth", "planning", "other"}),
    gold_path="catalog/references/eval_gold/grant_spend.jsonl",
    description="FAA AIP project intent: maintenance, growth, planning, or other",
)

FINANCE_VERIFY = EvaluationSpec(
    name="finance_verify",
    labels=frozenset(
        {
            "issued_grants",
            "program_budget",
            "project_list",
            "cip_proposed",
            "pfc",
            "bond",
            "other",
            "not_finance",
        }
    ),
    gold_path="catalog/references/eval_gold/finance_verify.jsonl",
    description="Official finance document kind from fetched excerpt",
)

PLAN_OUTLOOK = EvaluationSpec(
    name="plan_outlook",
    labels=frozenset({"growing", "declining", "maintaining"}),
    gold_path="catalog/references/eval_gold/plan_outlook.jsonl",
    description="Planning trajectory band from verified plan excerpt",
)

HUB_LINK = EvaluationSpec(
    name="hub_link",
    labels=frozenset({"master_plan", "alp", "chapter", "unknown"}),
    gold_path="catalog/references/eval_gold/hub_link.jsonl",
    description="Hub link kind guess from URL and anchor text",
)

BUDGET_LINE = EvaluationSpec(
    name="budget_line",
    labels=frozenset({"program", "fund", "project", "airport_allocation"}),
    gold_path="catalog/references/eval_gold/budget_line.jsonl",
    description="State aviation budget row grouping",
)

EVALUATIONS: dict[str, EvaluationSpec] = {
    spec.name: spec
    for spec in (GRANT_SPEND, FINANCE_VERIFY, PLAN_OUTLOOK, HUB_LINK, BUDGET_LINE)
}


def get_evaluation(name: str) -> EvaluationSpec:
    spec = EVALUATIONS.get(name)
    if spec is None:
        raise KeyError(f"unknown evaluation: {name}")
    return spec
