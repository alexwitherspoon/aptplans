#!/usr/bin/env python3
"""Replay evaluation gold against rubric classifiers. Not CI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_gold(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _eval_grant_spend(generate_fn) -> tuple[int, int]:
    from catalog.grants import grant_spend_category
    from catalog.models import Grant
    from pipeline.queries import classify_grant_spend

    gold_path = ROOT / "catalog/references/eval_gold/grant_spend.jsonl"
    ok = 0
    total = 0
    for row in _load_gold(gold_path):
        grant = Grant(
            airport_lid=row.get("lid") or "PDX",
            description=row["description"],
            is_planning=row.get("gold") == "planning",
        )
        rule = grant_spend_category(grant)
        scored = classify_grant_spend(
            description=grant.description,
            generate_fn=generate_fn,
            lid=grant.airport_lid,
            rule_category=rule,
        )
        predicted = scored["spend_category"]
        total += 1
        if predicted == row["gold"]:
            ok += 1
        else:
            print(f"miss: {row['description']!r} gold={row['gold']} got={predicted}")
    return ok, total


def _eval_budget_line(generate_fn) -> tuple[int, int]:
    from pipeline.queries import classify_budget_line

    gold_path = ROOT / "catalog/references/eval_gold/budget_line.jsonl"
    ok = 0
    total = 0
    for row in _load_gold(gold_path):
        scored = classify_budget_line(
            category=row["category"],
            note=row.get("note") or "",
            state=row.get("state") or "",
            generate_fn=generate_fn,
            rule_kind="program",
        )
        predicted = scored["line_kind"]
        total += 1
        if predicted == row["gold"]:
            ok += 1
        else:
            print(f"miss: {row['category']!r} gold={row['gold']} got={predicted}")
    return ok, total


def main() -> int:
    parser = argparse.ArgumentParser(description="Score rubric classifiers against gold fixtures")
    parser.add_argument(
        "--task",
        choices=("grant_spend", "budget_line", "all"),
        default="grant_spend",
    )
    parser.add_argument("--mock", action="store_true", help="Use rule fallback only (no Ollama)")
    args = parser.parse_args()

    if args.mock:
        generate_fn = None
    else:
        import os

        os.environ.setdefault("APTPLANS_LLM_PREDICT", "128")
        from pipeline.ollama import generate

        generate_fn = generate

    runners = {
        "grant_spend": _eval_grant_spend,
        "budget_line": _eval_budget_line,
    }
    tasks = list(runners) if args.task == "all" else [args.task]
    for name in tasks:
        ok, total = runners[name](generate_fn)
        print(f"{name}: {ok}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
