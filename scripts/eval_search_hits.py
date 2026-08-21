"""Score gated search-hit triage against gold packets. Live Ollama; not CI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.queries import allowed_hit_urls, evaluate_search_hit


def _in(value, allowed) -> bool:
    if isinstance(allowed, list):
        return value in allowed
    return value == allowed


def decision_ok(pred: dict, gold: dict, packet: list[str]) -> tuple[bool, str]:
    if pred["same_airport"] != gold["same_airport"]:
        return False, "same_airport"
    if pred["fetch"] != gold["fetch"]:
        return False, "fetch"
    packet_set = set(packet)
    if any(url not in packet_set for url in pred["artifact_urls"] + pred["page_urls"]):
        return False, "invented_url"
    for url in gold.get("forbid_urls") or []:
        if url in pred["artifact_urls"] or url in pred["page_urls"]:
            return False, "forbid_url"
    if gold["fetch"] == "yes":
        need_art = set(gold.get("artifact_urls") or [])
        need_page = set(gold.get("page_urls") or [])
        if need_art and not need_art <= set(pred["artifact_urls"]):
            return False, "missing_artifact"
        if need_page and not need_page <= set(pred["page_urls"]):
            return False, "missing_page"
    elif pred["artifact_urls"] and gold["fetch"] == "no":
        return False, "enqueue_on_no"
    return True, "ok"


def generate_json(prompt: str) -> str:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
    predict = int(os.environ.get("APTPLANS_LLM_PREDICT") or "256")
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "think": False,
        "keep_alive": -1,
        "options": {"temperature": 0, "num_predict": predict},
    }
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=1800) as resp:
        body = json.loads(resp.read().decode())
    text = (body.get("response") or "").strip()
    if not text:
        raise RuntimeError("empty ollama response")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval search-hit triage against gold fixtures")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--ids", default="")
    args = parser.parse_args()
    cases = json.loads((ROOT / "catalog" / "references" / "search_hits.json").read_text())["cases"]
    if args.ids:
        wanted = {item.strip() for item in args.ids.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    print(
        f"model={os.environ.get('OLLAMA_MODEL', 'qwen2.5:7b-instruct')} n={len(cases)}",
        flush=True,
    )
    wins = 0
    kind_wins = 0
    hit_wins = 0
    failures: list[str] = []
    for case in cases:
        gold = case["gold"]
        packet = allowed_hit_urls(
            artifact_url=case.get("artifact_url") or "",
            page_url=case.get("page_url") or "",
            prose=case.get("prose") or "",
        )
        print(f"start {case['id']}", flush=True)
        pred = evaluate_search_hit(
            lid=case["lid"],
            name=case["name"],
            query=case["query"],
            generate_fn=generate_json,
            artifact_url=case.get("artifact_url") or "",
            page_url=case.get("page_url") or "",
            prose=case.get("prose") or "",
            city=case.get("city") or "",
            state=case.get("state") or "",
            provider=case.get("provider") or "",
        )
        ok, why = decision_ok(pred, gold, packet)
        kind = _in(pred["kind_guess"], gold["kind_guess"])
        hit = _in(pred["hit_type"], gold["hit_type"])
        if ok:
            wins += 1
        else:
            failures.append(case["id"])
        if kind:
            kind_wins += 1
        if hit:
            hit_wins += 1
        mark = "ok" if ok else f"FAIL:{why}"
        print(
            f"{mark:16} {case['id']:24} fetch={pred['fetch']:<11} "
            f"kind={pred['kind_guess']:<12} hit={pred['hit_type']:<10} "
            f"{pred.get('reason', '')[:60]}",
            flush=True,
        )
    n = len(cases) or 1
    print(
        json.dumps(
            {
                "n": len(cases),
                "decision_pct": round(100 * wins / n, 1),
                "kind_pct": round(100 * kind_wins / n, 1),
                "hit_type_pct": round(100 * hit_wins / n, 1),
                "failures": failures,
            },
            indent=2,
        )
    )
    return 0 if wins / n >= 0.95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
