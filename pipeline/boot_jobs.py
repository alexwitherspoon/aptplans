"""Background maintenance jobs for the worker queue.

Deploy, timers, and the worker scheduler enqueue these instead of running long work inline.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from pipeline.fetch import fetch_bytes, post_json
from pipeline.ollama import llm_calls_enabled
from pipeline.queue import JobQueue, QueueJob
from pipeline.refresh import ROOT, overlay_dir_from_env, overlays_need_fetch
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

SCHEDULER_JOB_KINDS = (
    "pipeline_snapshot",
    "discovery",
    "link_check",
)

MAINTENANCE_JOB_KINDS = BOOT_JOB_KINDS + SCHEDULER_JOB_KINDS


def maintenance_job(kind: str) -> QueueJob:
    return QueueJob(
        kind=kind,
        document_id=None,
        source_url=None,
        airport_lid=None,
    )


def enqueue_job(queue_dir: Path | None, kind: str) -> bool:
    """Enqueue one maintenance job when that kind is not already pending or active."""
    if kind not in MAINTENANCE_JOB_KINDS:
        raise ValueError(f"unknown maintenance job kind: {kind}")
    root = queue_dir_from_env(queue_dir)
    queue = JobQueue(root)
    if queue.has_kind(kind):
        log.info("%s already queued", kind)
        return False
    queue.enqueue(maintenance_job(kind))
    log.info("%s queued", kind)
    return True


def enqueue_pipeline_snapshot(queue_dir: Path | None = None) -> bool:
    return enqueue_job(queue_dir, "pipeline_snapshot")


def enqueue_boot_jobs(queue_dir: Path | None = None) -> list[str]:
    """Queue background work after deploy or worker restart."""
    enqueued: list[str] = []
    overlay = overlay_dir_from_env()

    if enqueue_job(queue_dir, "pipeline_snapshot"):
        enqueued.append("pipeline_snapshot")

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


def enqueue_monthly_refresh(queue_dir: Path | None = None) -> list[str]:
    """Queue monthly FAA/grant refresh or follow-up jobs when overlays are current."""
    enqueued: list[str] = []
    if os.environ.get("APTPLANS_REFRESH_AIRPORTS") == "1":
        if enqueue_job(queue_dir, "overlay_refresh"):
            enqueued.append("overlay_refresh")
        return enqueued
    return enqueue_post_overlay_refresh(queue_dir)


def run_pipeline_snapshot(
    overlay_dir: Path | None = None,
    queue_dir: Path | None = None,
    catalog_root: Path | None = None,
) -> str:
    from pipeline.pipeline_status import build_public_snapshot

    overlay = overlay_dir_from_env(overlay_dir)
    queue = queue_dir_from_env(queue_dir)
    build_public_snapshot(overlay, queue, catalog_root=catalog_root or ROOT / "catalog")
    return "ok"


def run_discovery(overlay_dir: Path | None = None, queue_dir: Path | None = None) -> str:
    from pipeline.discover_overlay import discover_next_airports
    from pipeline.pipeline_status import record_discovery

    overlay = overlay_dir_from_env(overlay_dir)
    queue = queue_dir_from_env(queue_dir)
    result = discover_next_airports(overlay, queue)
    if result.get("skipped"):
        return "skipped"
    if result.get("airports"):
        record_discovery(overlay, list(result.get("airports") or []))
    if result.get("explore_jobs") or result.get("fetch_jobs"):
        return "ok"
    return "skipped"


def run_link_check(
    overlay_dir: Path | None = None,
    queue_dir: Path | None = None,
    catalog_root: Path | None = None,
) -> str:
    from pipeline.check import run_check_pass
    from pipeline.site_build import enqueue_site_build

    overlay = overlay_dir_from_env(overlay_dir)
    queue_path = queue_dir_from_env(queue_dir)
    count = run_check_pass(
        overlay_dir=overlay,
        catalog_root=catalog_root or ROOT / "catalog",
        queue_dir=queue_path,
    )
    if count:
        enqueue_site_build(queue_path)
        return "ok"
    return "skipped"


def run_overlay_refresh(overlay_dir: Path | None = None) -> str:
    if os.environ.get("APTPLANS_REFRESH_AIRPORTS") != "1":
        return "skipped"
    overlay = overlay_dir_from_env(overlay_dir)
    from pipeline.refresh_airports import refresh_airports
    from pipeline.refresh_grants import maybe_refresh_grants

    airports_refreshed = False
    if overlays_need_fetch(overlay):
        log.info("FAA airport overlay fetch starting")
        refresh_airports(overlay, fetch=fetch_bytes, sleep=time.sleep)
        airports_refreshed = True
        log.info("FAA airport overlay fetch finished")
    grant_count = maybe_refresh_grants(
        overlay, fetch=fetch_bytes, sleep=time.sleep, post_json=post_json
    )
    if airports_refreshed or grant_count:
        return "ok"
    return "skipped"


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
    """Queue follow-up work after overlay_refresh completes."""
    enqueued: list[str] = []
    if enqueue_job(queue_dir, "pipeline_snapshot"):
        enqueued.append("pipeline_snapshot")
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
        help="Enqueue follow-up jobs after overlay_refresh",
    )
    parser.add_argument(
        "--monthly",
        action="store_true",
        help="Enqueue monthly FAA/grant refresh or follow-up jobs",
    )
    parser.add_argument(
        "--discovery",
        action="store_true",
        help="Enqueue one discovery search pass",
    )
    parser.add_argument(
        "--link-check",
        action="store_true",
        help="Enqueue the daily official URL check",
    )
    args = parser.parse_args()
    if args.boot:
        enqueue_boot_jobs()
    elif args.post_overlay:
        enqueue_post_overlay_refresh()
    elif args.monthly:
        enqueue_monthly_refresh()
    elif args.discovery:
        enqueue_job(None, "discovery")
    elif args.link_check:
        enqueue_job(None, "link_check")
    else:
        parser.error("pass --boot, --post-overlay, --monthly, --discovery, or --link-check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
