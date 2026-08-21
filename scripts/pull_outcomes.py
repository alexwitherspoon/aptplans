"""Pull private review API signals into gitignored local files for scoring.

Uses APTPLANS_REVIEW_TOKEN from gitignored `.env` or `.env.review`.
Does not print the token. Failed artifacts (90-day origin reject store) are
copied next to labeled gold so training can use failures and successes.
Does not commit excerpts. Not CI. Not a publish.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.evidence import load_score_gold, score_packet, Packet
from pipeline.outcomes import GOLD_FIELDS
from pipeline.reject import training_case
from pipeline.review_client import review_credentials, review_get_bytes, review_request

DEFAULT_OUT = ROOT / "data" / "score" / "review"


def _rescore_gold(cases: list[dict]) -> list[dict]:
    """URL+label rescore of labeled outcomes against the current weights."""
    misses = []
    for case in cases:
        gold = case.get("gold") or {}
        packet = Packet(
            lid=case.get("lid") or "",
            name=case.get("name") or "",
            url=case.get("url") or "",
            label=case.get("label") or "",
        )
        scored = score_packet(packet)
        fail = [field for field in GOLD_FIELDS if field in gold and scored.get(field) != gold.get(field)]
        if fail:
            misses.append(
                {
                    "id": case.get("id"),
                    "url": case.get("url"),
                    "lid": case.get("lid"),
                    "fail": fail,
                    "gold": {field: gold.get(field) for field in GOLD_FIELDS},
                    "got": {field: scored.get(field) for field in GOLD_FIELDS},
                }
            )
    return misses


def _new_gold(cases: list[dict]) -> list[dict]:
    known = {(row.get("url") or "").rstrip("/") for row in load_score_gold().get("cases") or []}
    out = []
    for case in cases:
        url = (case.get("url") or "").rstrip("/")
        if url and url not in known:
            out.append(case)
    return out


def _merge_cases(*groups: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for group in groups:
        for case in group:
            url = (case.get("url") or "").rstrip("/")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(case)
    return out


def _pull_rejects(token: str, base: str, dest: Path) -> tuple[list[dict], list[dict], int]:
    payload = review_request("/v1/rejects", token=token, base=base)
    rows = payload.get("rejects") or []
    files_dir = dest / "rejects"
    files_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    stored = 0
    for row in rows:
        sha = row.get("sha256") or ""
        suffix = row.get("suffix") or ".bin"
        source = None
        if row.get("stored") and sha:
            data = review_get_bytes(f"/v1/rejects/{sha}/bytes", token=token, base=base)
            if data:
                path = files_dir / f"{sha}{suffix}"
                path.write_bytes(data)
                source = str(path.relative_to(ROOT))
                stored += 1
        case = training_case(row, source=source)
        if case:
            cases.append(case)
    (dest / "rejects.json").write_text(
        json.dumps({"n": len(rows), "rejects": rows}, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return rows, cases, stored


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull review API buckets, gold, and 90-day reject artifacts"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--url", help="Override APTPLANS_REVIEW_URL")
    parser.add_argument("--token", help="Override APTPLANS_REVIEW_TOKEN (prefer .env.review)")
    args = parser.parse_args()
    token, base = review_credentials()
    if args.token:
        token = args.token.strip()
    if args.url:
        base = args.url.rstrip("/")
    payload = review_request("/v1/signals", token=token, base=base)
    labeled = payload.get("gold") or []
    status = review_request("/v1/status", token=token, base=base)
    logs = review_request("/v1/logs?n=100", token=token, base=base)
    reject_rows, reject_cases, reject_files = _pull_rejects(token, base, args.out)
    gold = _merge_cases(labeled, reject_cases)
    live_misses = _rescore_gold(gold)
    novel = _new_gold(gold)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "signals.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (args.out / "gold.json").write_text(
        json.dumps({"n": len(gold), "cases": gold}, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "status.json").write_text(
        json.dumps(status, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (args.out / "logs.json").write_text(
        json.dumps(logs, indent=2, default=str) + "\n", encoding="utf-8"
    )
    summary = {
        "wrote": str(args.out),
        "base": base,
        "stats": payload.get("stats"),
        "queue": (status.get("queue") if isinstance(status, dict) else None),
        "gold": len(gold),
        "gold_labeled": len(labeled),
        "gold_from_rejects": len(reject_cases),
        "gold_not_in_score_gold": len(novel),
        "rejects": len(reject_rows),
        "reject_files": reject_files,
        "stored_disagreements": len(payload.get("disagreements") or []),
        "live_url_label_misses": len(live_misses),
        "uncertain": len(payload.get("uncertain") or []),
        "needs_human": len(payload.get("needs_human") or []),
        "failed": len(payload.get("failed") or []),
        "accepted": len(payload.get("accepted") or []),
        "next": [
            "inspect data/score/review/signals.json buckets",
            "inspect data/score/review/rejects.json and rejects/",
            "python3 scripts/train_evidence.py --outcomes data/score/review/gold.json",
            "merge new official URLs into catalog/references/score_gold.json (no excerpts)",
        ],
    }
    if live_misses:
        summary["misses"] = live_misses[:20]
    if novel:
        summary["new_gold_urls"] = [
            {"id": row.get("id"), "lid": row.get("lid"), "url": row.get("url")}
            for row in novel[:20]
        ]
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
