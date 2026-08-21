"""Fit evidence weights against full original gold sources. Not CI. Not a publish.

Loads packets once (extracted text is cached under data/score/extract), then runs
named-check ablation, coordinate descent on weights and thresholds, and random
mutations. Target is >=95% case accuracy on sourced gold, and >=95% field accuracy
overall.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.evidence import (
    ScoreConfig,
    features,
    gold_source_path,
    load_gold_packets,
    score_packet,
)

FIELDS = ("same_airport", "kind", "confirm", "explore", "publish")
DELTA = (-6.0, -3.0, -2.0, -1.0, 1.0, 2.0, 3.0, 6.0)
THRESHOLDS = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0)


def _eval(rows: list[tuple[dict, object, dict]], config: ScoreConfig | None = None) -> dict:
    cases = []
    field_hits = {field: 0 for field in FIELDS}
    field_n = {field: 0 for field in FIELDS}
    kind_conf: dict[tuple[str, str], int] = defaultdict(int)
    ok = 0
    sourced = 0
    sourced_ok = 0
    for case, packet, feats in rows:
        scored = score_packet(packet, config, feats=feats)
        gold = case.get("gold") or {}
        fail = [field for field in FIELDS if scored.get(field) != gold.get(field)]
        has_source = gold_source_path(case, cache=True) is not None or bool(packet.body)
        if has_source:
            sourced += 1
            if not fail:
                sourced_ok += 1
        if not fail:
            ok += 1
        for field in FIELDS:
            field_n[field] += 1
            if scored.get(field) == gold.get(field):
                field_hits[field] += 1
        kind_conf[(str(gold.get("kind")), str(scored.get("kind")))] += 1
        cases.append(
            {
                "id": case.get("id"),
                "shape": case.get("shape"),
                "ok": not fail,
                "fail": fail,
                "sourced": has_source,
                "gold": {field: gold.get(field) for field in FIELDS},
                "got": {field: scored.get(field) for field in FIELDS},
                "kind_scores": scored.get("kind_scores"),
                "confirm_score": scored.get("confirm_score"),
                "explore_score": scored.get("explore_score"),
                "publish_score": scored.get("publish_score"),
                "body_chars": len(packet.body),
                "features_on": {
                    key: value
                    for key, value in (scored.get("features") or {}).items()
                    if value
                },
            }
        )
    n = len(cases) or 1
    field_total = sum(field_n.values()) or 1
    field_correct = sum(field_hits.values())
    return {
        "n": len(cases),
        "case_ok": ok,
        "case_accuracy": ok / n,
        "sourced": sourced,
        "sourced_ok": sourced_ok,
        "sourced_accuracy": (sourced_ok / sourced) if sourced else 1.0,
        "field_accuracy": field_correct / field_total,
        "field_hits": {field: field_hits[field] / field_n[field] for field in FIELDS},
        "kind_confusion": {
            f"{want}->{got}": count for (want, got), count in sorted(kind_conf.items())
        },
        "misses": [row for row in cases if not row["ok"]],
        "cases": cases,
    }


def _score_key(report: dict) -> tuple[float, float, int]:
    return (report["case_accuracy"], report["field_accuracy"], -len(report["misses"]))


def ablation(rows: list[tuple[dict, object, dict]]) -> list[dict]:
    baseline = rows[0][2]
    out = []
    for key in sorted(baseline):
        zeroed_cfg = ScoreConfig.default()
        for table_name, table in (
            ("confirm", zeroed_cfg.weights_confirm),
            ("explore", zeroed_cfg.weights_explore),
            ("publish", zeroed_cfg.weights_publish),
        ):
            if key in table:
                table[key] = 0.0
        for kind_table in zeroed_cfg.kind_weights.values():
            if key in kind_table:
                kind_table[key] = 0.0
        report = _eval(rows, zeroed_cfg)
        out.append(
            {
                "feature": key,
                "case_accuracy": report["case_accuracy"],
                "field_accuracy": report["field_accuracy"],
                "misses": [row["id"] for row in report["misses"]],
            }
        )
    out.sort(key=lambda row: (row["case_accuracy"], row["field_accuracy"]))
    return out


def coordinate_descent(rows, rounds: int = 3) -> tuple[dict, ScoreConfig, list]:
    best_cfg = ScoreConfig.default()
    best = _eval(rows, best_cfg)
    log = [{"init": True, **{k: best[k] for k in ("case_accuracy", "field_accuracy")}}]
    tables = [
        ("confirm", best_cfg.weights_confirm),
        ("explore", best_cfg.weights_explore),
        ("publish", best_cfg.weights_publish),
    ]
    for _ in range(rounds):
        for table_name, table in tables:
            for key in list(table):
                base = table[key]
                for delta in DELTA:
                    table[key] = base + delta
                    report = _eval(rows, best_cfg)
                    if _score_key(report) > _score_key(best):
                        best = report
                        base = table[key]
                        log.append(
                            {
                                "table": table_name,
                                "key": key,
                                "value": base,
                                "case_accuracy": best["case_accuracy"],
                                "field_accuracy": best["field_accuracy"],
                                "misses": [row["id"] for row in best["misses"]],
                            }
                        )
                    else:
                        table[key] = base
        for kind, table in best_cfg.kind_weights.items():
            for key in list(table):
                base = table[key]
                for delta in DELTA:
                    table[key] = base + delta
                    report = _eval(rows, best_cfg)
                    if _score_key(report) > _score_key(best):
                        best = report
                        base = table[key]
                        log.append(
                            {
                                "table": f"kind.{kind}",
                                "key": key,
                                "value": base,
                                "case_accuracy": best["case_accuracy"],
                                "field_accuracy": best["field_accuracy"],
                                "misses": [row["id"] for row in best["misses"]],
                            }
                        )
                    else:
                        table[key] = base
        for attr in ("confirm_threshold", "explore_threshold", "publish_threshold", "kind_threshold"):
            base = getattr(best_cfg, attr)
            for value in THRESHOLDS:
                setattr(best_cfg, attr, value)
                report = _eval(rows, best_cfg)
                if _score_key(report) > _score_key(best):
                    best = report
                    base = value
                    log.append(
                        {
                            "threshold": attr,
                            "value": value,
                            "case_accuracy": best["case_accuracy"],
                            "field_accuracy": best["field_accuracy"],
                            "misses": [row["id"] for row in best["misses"]],
                        }
                    )
                else:
                    setattr(best_cfg, attr, base)
    return best, best_cfg, log


def mutate(rows, config: ScoreConfig, n: int, seed: int) -> tuple[dict, ScoreConfig, list]:
    rng = random.Random(seed)
    best_cfg = copy.deepcopy(config)
    best = _eval(rows, best_cfg)
    log = []
    tables = [
        best_cfg.weights_confirm,
        best_cfg.weights_explore,
        best_cfg.weights_publish,
        *best_cfg.kind_weights.values(),
    ]
    for _ in range(n):
        trial = copy.deepcopy(best_cfg)
        pool = [
            trial.weights_confirm,
            trial.weights_explore,
            trial.weights_publish,
            *trial.kind_weights.values(),
        ]
        table = rng.choice(pool)
        if not table:
            continue
        key = rng.choice(list(table))
        table[key] = table[key] + rng.choice(DELTA)
        if rng.random() < 0.2:
            attr = rng.choice(
                ("confirm_threshold", "explore_threshold", "publish_threshold", "kind_threshold")
            )
            setattr(trial, attr, rng.choice(THRESHOLDS))
        report = _eval(rows, trial)
        if _score_key(report) >= _score_key(best):
            best = report
            best_cfg = trial
            log.append(
                {
                    "case_accuracy": best["case_accuracy"],
                    "field_accuracy": best["field_accuracy"],
                    "misses": [row["id"] for row in best["misses"]],
                }
            )
    return best, best_cfg, log


def config_payload(config: ScoreConfig) -> dict:
    return {
        "weights_confirm": config.weights_confirm,
        "weights_explore": config.weights_explore,
        "weights_publish": config.weights_publish,
        "kind_weights": config.kind_weights,
        "confirm_threshold": config.confirm_threshold,
        "explore_threshold": config.explore_threshold,
        "publish_threshold": config.publish_threshold,
        "kind_threshold": config.kind_threshold,
    }


def _load_extra_cases(path: Path | None) -> list[dict]:
    if path is None:
        return []
    target = path / "gold.json" if path.is_dir() else path
    if not target.is_file():
        raise SystemExit(f"outcomes file not found: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    return list(payload.get("cases") or [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Train/eval evidence weights on full gold sources")
    parser.add_argument("--committed", action="store_true")
    parser.add_argument("--fit", action="store_true", help="Coordinate descent then mutate")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--mutations", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--ablate", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--outcomes",
        type=Path,
        help="Labeled gold from make pull-outcomes (file or data/score/review dir)",
    )
    args = parser.parse_args()
    extra = _load_extra_cases(args.outcomes)
    print("loading packets (extract cache under data/score/extract)...", file=sys.stderr, flush=True)
    loaded = load_gold_packets(cache=not args.committed, extra=extra)
    rows = [(case, packet, features(packet)) for case, packet in loaded]
    print(
        f"scored {len(rows)} packets ({len(extra)} from outcomes)",
        file=sys.stderr,
        flush=True,
    )
    baseline = _eval(rows)
    result = {
        "baseline": {
            "n": baseline["n"],
            "case_accuracy": baseline["case_accuracy"],
            "sourced_accuracy": baseline["sourced_accuracy"],
            "field_accuracy": baseline["field_accuracy"],
            "field_hits": baseline["field_hits"],
            "kind_confusion": baseline["kind_confusion"],
            "misses": baseline["misses"],
        }
    }
    config = ScoreConfig.default()
    best = baseline
    if args.ablate:
        result["ablation_worst"] = ablation(rows)[:12]
    if args.fit:
        best, config, descent_log = coordinate_descent(rows, rounds=args.rounds)
        mut_best, mut_cfg, mut_log = mutate(rows, config, n=args.mutations, seed=args.seed)
        if _score_key(mut_best) > _score_key(best):
            best, config = mut_best, mut_cfg
        result["fit"] = {
            "case_accuracy": best["case_accuracy"],
            "sourced_accuracy": best["sourced_accuracy"],
            "field_accuracy": best["field_accuracy"],
            "field_hits": best["field_hits"],
            "kind_confusion": best["kind_confusion"],
            "misses": best["misses"],
            "descent_steps": descent_log,
            "mutation_improvements": mut_log,
            "config": config_payload(config),
        }
    if args.out:
        args.out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    summary = {
        "n": best["n"],
        "case_accuracy": round(best["case_accuracy"], 4),
        "sourced_accuracy": round(best["sourced_accuracy"], 4),
        "field_accuracy": round(best["field_accuracy"], 4),
        "field_hits": {k: round(v, 4) for k, v in best["field_hits"].items()},
        "misses": [
            {"id": row["id"], "fail": row["fail"], "got": row["got"], "gold": row["gold"]}
            for row in best["misses"]
        ],
        "outcomes": len(extra),
        "target_met": best["case_accuracy"] >= 0.95 and best["field_accuracy"] >= 0.95,
    }
    print(json.dumps(summary, indent=2))
    return 0 if summary["target_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
