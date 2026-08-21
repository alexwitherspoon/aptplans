"""Score gated search-hint queries against gold packets. Live Ollama; not CI."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.queries import evaluate_search_hints

_SITE_RE = re.compile(r"\bsite:([a-z0-9.-]+)", re.I)


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


def hint_ok(pred: dict, gold: dict) -> tuple[bool, str]:
    queries = [item["query"] for item in pred.get("queries") or []]
    if gold.get("forbid_http") and any("http://" in item or "https://" in item for item in queries):
        return False, "http_url"
    allow = {host.lower() for host in gold.get("allow_hosts") or []}
    for query in queries:
        for host in _SITE_RE.findall(query):
            host = host.lower().removeprefix("www.")
            if allow and not any(host == item or host.endswith("." + item) for item in allow):
                return False, "off_host"
    must = [token.lower() for token in gold.get("must_include") or []]
    any_of = [token.lower() for token in gold.get("must_include_any") or []]
    for query in queries:
        blob = query.lower()
        if must and not all(token in blob for token in must):
            continue
        if any_of and not any(token in blob for token in any_of):
            continue
        return True, "ok"
    return False, "no_matching_query"


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval gated search-hint queries against gold")
    parser.add_argument("--ids", default="")
    args = parser.parse_args()
    cases = json.loads((ROOT / "catalog" / "references" / "search_hints.json").read_text())["cases"]
    if args.ids:
        wanted = {item.strip() for item in args.ids.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in wanted]
    print(
        f"model={os.environ.get('OLLAMA_MODEL', 'qwen2.5:7b-instruct')} n={len(cases)}",
        flush=True,
    )
    wins = 0
    failures: list[str] = []
    for case in cases:
        print(f"start {case['id']}", flush=True)
        pred = evaluate_search_hints(
            lid=case["lid"],
            name=case["name"],
            generate_fn=generate_json,
            hits=case["hits"],
            ran_queries=case.get("ran_queries") or [],
            missing=case.get("missing") or [],
            website=case.get("website") or "",
            city=case.get("city") or "",
            state=case.get("state") or "",
        )
        ok, why = hint_ok(pred, case["gold"])
        if ok:
            wins += 1
        else:
            failures.append(case["id"])
        mark = "ok" if ok else f"FAIL:{why}"
        shown = "; ".join(item["query"] for item in pred["queries"]) or "(none)"
        print(f"{mark:20} {case['id']:22} {shown[:100]}", flush=True)
    n = len(cases) or 1
    print(
        json.dumps(
            {
                "n": len(cases),
                "decision_pct": round(100 * wins / n, 1),
                "failures": failures,
            },
            indent=2,
        )
    )
    return 0 if wins / n >= 0.75 else 1


if __name__ == "__main__":
    raise SystemExit(main())
