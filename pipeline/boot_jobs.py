"""Background maintenance jobs for the worker queue.

Deploy and worker boot enqueue these instead of running long work inline.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from pipeline.fetch import fetch_bytes, post_json
from pipeline.ollama import llm_calls_enabled
from pipeline.queue import JobQueue, QueueJob
from pipeline.refresh import overlay_dir_from_env, overlays_need_fetch
from pipeline.status import queue_dir_from_env

log = logging.getLogger("aptplans.boot_jobs")

BOOT_JOB_KINDS = (
    "overlay_refresh",
    "grant_spend",
    "budget_enrich",
    "overview_refresh",
    "search_sync",
    "ollama_warm",
    "site_build",
)


def maintenance_job(kind: str) -> QueueJob:
    return QueueJob(
        kind=kind,
        document_id=None,
        source_url=None,
        airport_lid=None,
    )


def enqueue_job(queue_dir: Path | None, kind: str) -> bool:
    """Enqueue one maintenance job when that kind is not already pending or active."""
    if kind not in BOOT_JOB_KINDS:
        raise ValueError(f"unknown maintenance job kind: {kind}")
    root = queue_dir_from_env(queue_dir)
    queue = JobQueue(root)
    if queue.has_kind(kind):
        log.info("%s already queued", kind)
        return False
    queue.enqueue(maintenance_job(kind))
    log.info("%s queued", kind)
    return True


def enqueue_boot_jobs(queue_dir: Path | None = None) -> list[str]:
    """Queue background work after deploy or worker restart."""
    enqueued: list[str] = []
    overlay = overlay_dir_from_env()

    if os.environ.get("APTPLANS_REFRESH_AIRPORTS") == "1" and overlays_need_fetch(overlay):
        if enqueue_job(queue_dir, "overlay_refresh"):
            enqueued.append("overlay_refresh")

    if llm_calls_enabled():
        for kind in ("grant_spend", "budget_enrich", "ollama_warm"):
            if enqueue_job(queue_dir, kind):
                enqueued.append(kind)

    if "overlay_refresh" not in enqueued:
        for kind in ("overview_refresh", "search_sync", "site_build"):
            if enqueue_job(queue_dir, kind):
                enqueued.append(kind)
    return enqueued


def run_overlay_refresh(overlay_dir: Path | None = None) -> str:
    if os.environ.get("APTPLANS_REFRESH_AIRPORTS") != "1":
        return "skipped"
    overlay = overlay_dir_from_env(overlay_dir)
    if not overlays_need_fetch(overlay):
        return "skipped"
    from pipeline.refresh_airports import maybe_refresh

    log.info("overlay refresh starting")
    maybe_refresh(overlay, fetch=fetch_bytes, sleep=time.sleep, post_json=post_json)
    log.info("overlay refresh finished")
    return "ok"


def run_grant_spend(overlay_dir: Path | None = None) -> str:
    if not llm_calls_enabled():
        return "skipped"
    from pipeline.grant_classify import reclassify_grants_overlay

    overlay = overlay_dir_from_env(overlay_dir)
    count = reclassify_grants_overlay(overlay, sleep=time.sleep)
    return "ok" if count else "skipped"


def run_budget_enrich(overlay_dir: Path | None = None) -> str:
    if not llm_calls_enabled():
        return "skipped"
    from pipeline.refresh_budgets import maybe_enrich_budgets

    overlay = overlay_dir_from_env(overlay_dir)
    count = maybe_enrich_budgets(overlay)
    return "ok" if count else "skipped"


def run_overview_refresh(overlay_dir: Path | None = None) -> str:
    from pipeline.overviews import refresh_overviews

    overlay = overlay_dir_from_env(overlay_dir)
    count = refresh_overviews(overlay)
    return "ok" if count else "skipped"


def run_search_sync() -> str:
    from pipeline.search import boot_sync

    boot_sync()
    return "ok"


def run_ollama_warm() -> str:
    if not llm_calls_enabled():
        return "skipped"
    from pipeline.ollama import load_model

    load_model()
    return "ok"


def enqueue_post_overlay_refresh(queue_dir: Path | None = None) -> list[str]:
    """Queue follow-up work after a synchronous overlay refresh (monthly timer)."""
    enqueued: list[str] = []
    if llm_calls_enabled():
        for kind in ("grant_spend", "budget_enrich"):
            if enqueue_job(queue_dir, kind):
                enqueued.append(kind)
    for kind in ("overview_refresh", "search_sync", "site_build"):
        if enqueue_job(queue_dir, kind):
            enqueued.append(kind)
    return enqueued


def main() -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Enqueue background maintenance jobs")
    parser.add_argument("--boot", action="store_true", help="Enqueue the worker boot job set")
    parser.add_argument(
        "--post-overlay",
        action="store_true",
        help="Enqueue follow-up jobs after a synchronous overlay refresh",
    )
    args = parser.parse_args()
    if args.boot:
        enqueue_boot_jobs()
    elif args.post_overlay:
        enqueue_post_overlay_refresh()
    else:
        parser.error("pass --boot or --post-overlay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
