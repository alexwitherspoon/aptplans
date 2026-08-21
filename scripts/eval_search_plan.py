"""Replay the adaptive search ladder locally. Hits are signals, not a publish.

Default is fixture replay (no network, CI-safe). --explore GETs hub HTML.
--enqueue writes explore/fetch jobs into data/queue for make pipeline; review
stays pending until a separate vet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.explore import confirm_jobs, explore_page, followup_explore_jobs, hub_document_kind
from pipeline.local_env import load_local_env
from pipeline.queue import JobQueue, QueueJob
from pipeline.search_client import gemini_configured, gemini_escalate, search_hits, search_provider
from pipeline.search_plan import (
    SearchIdentity,
    hit_worth_confirm,
    hit_worth_explore,
    run_search_plan,
)
from pipeline.search_scope import (
    case_from_airport,
    in_search_scope,
    overlay_dir,
    parse_search_states,
    scoped_overlay_airports,
)
from pipeline.stages import source_family


def _fetch(url: str) -> tuple[str | None, int | None, str | None]:
    req = Request(url, headers={"User-Agent": os.environ.get("APTPLANS_USER_AGENT") or "aptplans.org"})
    try:
        with urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", "replace"), getattr(resp, "status", 200), None
    except HTTPError as exc:
        return None, exc.code, str(exc)
    except (URLError, OSError, TimeoutError) as exc:
        return None, None, str(exc)


def _identity_from_case(case: dict) -> SearchIdentity:
    return SearchIdentity(
        lid=case["airport_lid"],
        name=case["name"],
        city=case.get("city") or "",
        state=case.get("state") or "",
        website=case.get("website") or "",
    )


def _gold_urls(case: dict) -> list[str]:
    urls = []
    for doc in case.get("documents") or []:
        if doc.get("kind") not in {"master_plan", "alp"}:
            continue
        src = doc.get("source_url") or ""
        if src.startswith("http"):
            urls.append(src)
    return urls


def _explore_hubs(
    identity: SearchIdentity,
    urls: list[str],
) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for url in urls:
        if not url.startswith("http") or url in seen or url.lower().endswith(".pdf"):
            continue
        seen.add(url)
        html, status, error = _fetch(url)
        if html is None:
            rows.append(
                {
                    "url": url,
                    "source_family": source_family(status=status, error=error),
                    "error": error,
                    "n_confirm_jobs": 0,
                    "n_followup_explore": 0,
                    "confirm_urls": [],
                }
            )
            time.sleep(0.8)
            continue
        result = explore_page(html, url)
        jobs = confirm_jobs(result, airport_lid=identity.lid, state=identity.state)
        follows = followup_explore_jobs(result, airport_lid=identity.lid, state=identity.state)
        rows.append(
            {
                "url": url,
                "title": result.title,
                "hub_kind": hub_document_kind(result),
                "source_family": source_family(
                    status=status or 200,
                    n_artifacts=len(result.artifacts()),
                    n_followups=len(result.followups),
                    hub_kind=hub_document_kind(result),
                    page_url=url,
                ),
                "n_artifacts": len(result.artifacts()),
                "n_followups": len(result.followups),
                "n_confirm_jobs": len(jobs),
                "n_followup_explore": len(follows),
                "confirm_urls": [job.source_url for job in jobs],
                "followup_urls": [job.source_url for job in follows],
            }
        )
        time.sleep(0.8)
    return rows


def _report_case(
    case: dict,
    *,
    provider: str,
    explore: bool,
    enqueue: bool,
    queue_dir: Path,
    max_steps: int,
    generate_fn=None,
    escalate: bool = False,
) -> dict:
    identity = _identity_from_case(case)
    session = run_search_plan(
        identity,
        lambda query: search_hits(query, provider=provider),
        max_steps=max_steps,
        generate_fn=generate_fn,
        escalate_fn=gemini_escalate if escalate else None,
    )
    explore_urls = [
        hit.url for hit in session.hits if hit_worth_explore(hit, identity)
    ]
    if identity.website and identity.website not in explore_urls:
        explore_urls.insert(0, identity.website)
    confirm_from_search = [hit.url for hit in session.hits if hit_worth_confirm(hit)]
    explored = _explore_hubs(identity, explore_urls) if explore else []
    confirm_from_explore = [
        url for row in explored for url in row.get("confirm_urls") or [] if url
    ]
    found = list(dict.fromkeys(confirm_from_search + confirm_from_explore + explore_urls))
    gold = _gold_urls(case)
    gold_set = set(gold)
    found_set = set(found)
    recall = sorted(url for url in gold if url in found_set)
    if enqueue:
        queue = JobQueue(queue_dir)
        for url in explore_urls:
            queue.enqueue(
                QueueJob(
                    kind="explore",
                    document_id=(
                        f"{identity.lid.lower()}-site"
                        if url.rstrip("/") == (identity.website or "").rstrip("/")
                        else None
                    ),
                    source_url=url,
                    airport_lid=identity.lid,
                    state=identity.state,
                    suggested_kind="other",
                    found_on=url,
                )
            )
        for url in confirm_from_search:
            queue.enqueue(
                QueueJob(
                    kind="fetch",
                    document_id=None,
                    source_url=url,
                    airport_lid=identity.lid,
                    state=identity.state,
                    found_on="",
                )
            )
    return {
        "lid": identity.lid,
        "name": identity.name,
        "provider": provider,
        "queries": [
            {"kind": round.step.kind, "query": round.step.query, "why": round.step.why, "n_hits": len(round.hits)}
            for round in session.rounds
        ],
        "n_queries": len(session.rounds),
        "explore_urls": explore_urls,
        "confirm_from_search": confirm_from_search,
        "explored": explored,
        "confirm_from_explore": confirm_from_explore,
        "gold": gold,
        "gold_recall": recall,
        "gold_recall_n": len(recall),
        "gold_n": len(gold),
        "note": "Signals and confirm candidates only. Not a publish.",
    }


def _ollama_json(prompt: str) -> str:
    import urllib.request

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


def _select_cases(args, provider: str) -> list[dict]:
    states = parse_search_states("*" if args.all_states else args.states)
    if args.lid:
        lid = args.lid.strip().upper()
        cases = json.loads((ROOT / "catalog" / "references" / "cases.json").read_text())["cases"]
        found = [case for case in cases if case["airport_lid"] == lid]
        if found:
            return found
        for airport in scoped_overlay_airports(overlay_dir(), states=None):
            if airport.lid == lid:
                return [case_from_airport(airport)]
        raise SystemExit(f"no reference case or overlay airport for {lid}")
    if args.overlay:
        airports = scoped_overlay_airports(overlay_dir(), states=states, limit=args.limit)
        if not airports:
            raise SystemExit("no overlay airports in search scope (need NASR overlay and APTPLANS_SEARCH_STATES)")
        return [case_from_airport(airport) for airport in airports]
    cases = json.loads((ROOT / "catalog" / "references" / "cases.json").read_text())["cases"]
    if args.catalog:
        if provider != "fixture":
            cases = [case for case in cases if in_search_scope(case.get("state") or "", states)]
        if args.limit:
            cases = cases[: args.limit]
        return cases
    return [case for case in cases if case["airport_lid"] == "4S9"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Adaptive search ladder; not a publish")
    parser.add_argument("--lid", default="", help="One airport LID (bypasses state scope)")
    parser.add_argument("--catalog", action="store_true", help="cases.json airports (fixture replay ignores state scope)")
    parser.add_argument(
        "--overlay",
        action="store_true",
        help="NASR overlay airports in APTPLANS_SEARCH_STATES (default OR)",
    )
    parser.add_argument("--states", default=None, help="Override APTPLANS_SEARCH_STATES (comma codes, or *)")
    parser.add_argument("--all-states", action="store_true", help="Do not filter by state")
    parser.add_argument("--limit", type=int, default=0, help="Cap airports (0 = no cap)")
    parser.add_argument("--explore", action="store_true", help="GET hub pages (not CI)")
    parser.add_argument("--enqueue", action="store_true", help="Write pending jobs to data/queue")
    parser.add_argument("--provider", default="", help="fixture, brave, google, or gemini")
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--llm", action="store_true", help="Gated Ollama hints for follow-up queries (not CI)")
    parser.add_argument(
        "--escalate",
        action="store_true",
        help="After Brave stalls, one Gemini search for packets (needs APTPLANS_GEMINI_KEY)",
    )
    parser.add_argument("--no-escalate", action="store_true", help="Do not call Gemini even if a key is set")
    parser.add_argument(
        "--queue-dir",
        default="",
        help="Job queue directory (default APTPLANS_QUEUE or data/queue)",
    )
    args = parser.parse_args()
    load_local_env()
    provider = args.provider or search_provider()
    cases = _select_cases(args, provider)
    queue_dir = Path(
        args.queue_dir
        or os.environ.get("APTPLANS_QUEUE")
        or ROOT / "data" / "queue"
    )
    generate_fn = _ollama_json if args.llm else None
    escalate = False
    if not args.no_escalate and provider == "brave" and (args.escalate or gemini_configured()):
        escalate = True
    reports = [
        _report_case(
            case,
            provider=provider,
            explore=args.explore,
            enqueue=args.enqueue,
            queue_dir=queue_dir,
            max_steps=args.max_steps,
            generate_fn=generate_fn,
            escalate=escalate,
        )
        for case in cases
    ]
    print(
        json.dumps(
            {
                "provider": provider,
                "states": sorted(parse_search_states("*" if args.all_states else args.states) or ["*"]),
                "n": len(reports),
                "airports": reports,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
