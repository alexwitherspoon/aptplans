"""Adaptive search for scoped overlay airports; enqueue explore and fetch jobs."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

from pipeline.queue import JobQueue, QueueJob
from pipeline.search_client import gemini_configured, gemini_escalate, live_search_enabled, search_hits, search_provider
from pipeline.search_plan import SearchHit, SearchIdentity, SearchSession, hit_worth_confirm, hit_worth_explore, run_search_plan
from pipeline.search_scope import parse_search_states, scoped_overlay_airports

log = logging.getLogger("aptplans.discovery")

CURSOR_NAME = "discovery_cursor.json"
DEFAULT_LIMIT = 5
DEFAULT_MAX_STEPS = 5


def discovery_limit() -> int:
    raw = os.environ.get("APTPLANS_DISCOVERY_LIMIT", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_LIMIT


def discovery_max_steps() -> int:
    raw = os.environ.get("APTPLANS_DISCOVERY_MAX_STEPS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_MAX_STEPS


def _cursor_path(overlay_dir: Path) -> Path:
    return overlay_dir / CURSOR_NAME


def _load_cursor(overlay_dir: Path) -> dict:
    path = _cursor_path(overlay_dir)
    if not path.is_file():
        return {"index": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"index": 0}
    if not isinstance(payload, dict):
        return {"index": 0}
    return payload


def _save_cursor(overlay_dir: Path, cursor: dict) -> None:
    path = _cursor_path(overlay_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursor, indent=2) + "\n", encoding="utf-8")


def enqueue_search_session(
    queue: JobQueue,
    identity: SearchIdentity,
    session: SearchSession,
) -> tuple[int, int]:
    """Queue explore jobs for hubs and fetch jobs for plan-shaped search hits."""
    explore_urls = [hit.url for hit in session.hits if hit_worth_explore(hit, identity)]
    if identity.website and identity.website not in explore_urls:
        explore_urls.insert(0, identity.website)
    confirm_urls = [hit.url for hit in session.hits if hit_worth_confirm(hit)]

    explore_jobs = 0
    fetch_jobs = 0
    seen: set[str] = set()
    for url in explore_urls:
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
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
        explore_jobs += 1
    for url in confirm_urls:
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
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
        fetch_jobs += 1
    return explore_jobs, fetch_jobs


def discover_next_airports(
    overlay_dir: Path,
    queue_dir: Path,
    *,
    limit: int | None = None,
    max_steps: int | None = None,
    search_fn: Callable[[str], list[SearchHit]] | None = None,
    escalate_fn=None,
) -> dict:
    """Run the search ladder for the next airports in scope and enqueue jobs."""
    if not live_search_enabled():
        return {"skipped": "live_search_off", "explore_jobs": 0, "fetch_jobs": 0}

    states = parse_search_states()
    airports = scoped_overlay_airports(overlay_dir, states=states)
    if not airports:
        return {"skipped": "no_airports", "explore_jobs": 0, "fetch_jobs": 0}

    per_run = limit if limit is not None else discovery_limit()
    steps = max_steps if max_steps is not None else discovery_max_steps()
    provider = search_provider()
    search = search_fn or (lambda query: search_hits(query, provider=provider))
    escalate = None
    if escalate_fn is not None:
        escalate = escalate_fn
    elif provider == "brave" and gemini_configured():
        escalate = gemini_escalate

    cursor = _load_cursor(overlay_dir)
    index = int(cursor.get("index") or 0) % len(airports)
    queue = JobQueue(queue_dir)
    total_explore = 0
    total_fetch = 0
    processed: list[str] = []

    for _ in range(per_run):
        airport = airports[index % len(airports)]
        index += 1
        identity = SearchIdentity(
            lid=airport.lid,
            name=airport.name,
            city=airport.city or "",
            state=airport.state or "",
            website=airport.website or "",
        )
        session = run_search_plan(
            identity,
            search,
            max_steps=steps,
            escalate_fn=escalate,
        )
        explore_jobs, fetch_jobs = enqueue_search_session(queue, identity, session)
        total_explore += explore_jobs
        total_fetch += fetch_jobs
        processed.append(airport.lid)
        log.info(
            "discovery lid=%s explore_jobs=%s fetch_jobs=%s queries=%s",
            airport.lid,
            explore_jobs,
            fetch_jobs,
            len(session.rounds),
        )

    _save_cursor(overlay_dir, {"index": index % len(airports), "last_lids": processed})
    return {
        "airports": processed,
        "explore_jobs": total_explore,
        "fetch_jobs": total_fetch,
    }
