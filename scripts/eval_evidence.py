"""Score labeled gold packets against full original bytes. No network. Not a publish."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.evidence import (
    SCORE_CACHE,
    gold_source_path,
    load_score_gold,
    load_score_sample,
    packet_from_gold,
    score_packet,
)

FIELDS = ("same_airport", "kind", "confirm", "explore", "publish")


def _rel(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def eval_gold(*, cache: bool = True) -> dict:
    rows = []
    misses = 0
    missing_source = 0
    for case in load_score_gold().get("cases") or []:
        source = gold_source_path(case, cache=cache)
        packet = packet_from_gold(case, cache=cache)
        scored = score_packet(packet)
        gold = case.get("gold") or {}
        fail = [field for field in FIELDS if scored.get(field) != gold.get(field)]
        if fail:
            misses += 1
        if source is None:
            missing_source += 1
        rows.append(
            {
                "id": case.get("id"),
                "shape": case.get("shape"),
                "ok": not fail,
                "fail": fail,
                "source": _rel(source),
                "gold": {field: gold.get(field) for field in FIELDS},
                "got": {field: scored.get(field) for field in FIELDS},
                "confirm_score": scored.get("confirm_score"),
                "explore_score": scored.get("explore_score"),
                "publish_score": scored.get("publish_score"),
                "kind_scores": scored.get("kind_scores"),
                "body_chars": len(packet.body),
            }
        )
    n = len(rows) or 1
    field_n = len(rows) * len(FIELDS)
    field_ok = sum(len(FIELDS) - len(row["fail"]) for row in rows)
    return {
        "n": len(rows),
        "misses": misses,
        "missing_source": missing_source,
        "case_accuracy": (len(rows) - misses) / n,
        "field_accuracy": field_ok / field_n,
        "cache": str(SCORE_CACHE),
        "cases": rows,
    }


def sample_stats() -> dict:
    payload = load_score_sample()
    return {
        "description": payload.get("description"),
        "gold": payload.get("gold"),
        "committed_dir": payload.get("committed_dir"),
        "cache_dir": payload.get("cache_dir"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay evidence weights against score_gold.json full sources"
    )
    parser.add_argument("--sample", action="store_true", help="Print score_sample.json pointer only")
    parser.add_argument(
        "--committed",
        action="store_true",
        help="Score committed fixtures only (skip gitignored data/score)",
    )
    args = parser.parse_args()
    if args.sample:
        print(json.dumps(sample_stats(), indent=2))
        return 0
    report = eval_gold(cache=not args.committed)
    summary = {
        "n": report["n"],
        "misses": report["misses"],
        "missing_source": report["missing_source"],
        "case_accuracy": round(report["case_accuracy"], 4),
        "field_accuracy": round(report["field_accuracy"], 4),
        "fail": [row["id"] for row in report["cases"] if not row["ok"]],
    }
    print(json.dumps({"summary": summary, "report": report}, indent=2, default=str))
    return 1 if report["misses"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
