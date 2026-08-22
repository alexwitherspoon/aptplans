"""Compose worker. Drains the on-disk queue with airport-scoped concurrency."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from pipeline.fetch import fetch_bytes, post_json
from pipeline.pace import airport_concurrency, job_pause_seconds
from pipeline.queue import JobRetry
from pipeline.overviews import refresh_overviews
from pipeline.refresh import ROOT, overlay_dir_from_env, overlays_need_fetch
from pipeline.refresh_airports import maybe_refresh
from pipeline.refresh_budgets import maybe_enrich_budgets
from pipeline.grant_classify import reclassify_grants_overlay
from pipeline.run_once import process_next, refresh_public_site
from pipeline.search import boot_sync
from pipeline.service_log import attach_jsonl_handler
from pipeline.status import queue_dir_from_env

log = logging.getLogger("aptplans.worker")

BOOT_PAUSE_SECONDS = 5.0
DEFAULT_IDLE_SEC = 60.0
DEFAULT_INTAKE_SEC = 3600.0
DEFAULT_DISCOVERY_SEC = 86400.0


def cold_start_overlays(
    overlay_dir: Path | None = None,
    *,
    fetch=fetch_bytes,
    sleep=time.sleep,
    pause_before: float = BOOT_PAUSE_SECONDS,
    post_json=None,
) -> bool:
    """Fetch NASR, NPIAS, OurAirports home pages, and AIP grants if overlays are missing or stale. Never force."""
    if os.environ.get("APTPLANS_REFRESH_AIRPORTS") != "1":
        log.info("FAA overlay fetch off (APTPLANS_REFRESH_AIRPORTS unset)")
        return False
    overlay = overlay_dir or overlay_dir_from_env()
    if not overlays_need_fetch(overlay):
        log.info("FAA overlays present for this month; skip fetch")
        return False
    log.info(
        "FAA overlays missing or stale; wait %.0fs, then fetch one request at a time",
        pause_before,
    )
    if pause_before:
        sleep(pause_before)
    maybe_refresh(overlay, fetch=fetch, sleep=sleep, post_json=post_json)
    return True


def cold_start_grant_spend(overlay_dir: Path | None = None) -> bool:
    """Classify overlay grant spend when LLM is enabled (no FAA fetch)."""
    if os.environ.get("APTPLANS_LLM") != "1":
        return False
    overlay = overlay_dir or overlay_dir_from_env()
    try:
        return reclassify_grants_overlay(overlay, sleep=time.sleep) > 0
    except Exception:
        log.exception("grant spend reclassify failed")
        return False


def cold_start_budget_lines(overlay_dir: Path | None = None) -> bool:
    """Classify budget overlay rows when LLM is enabled."""
    overlay = overlay_dir or overlay_dir_from_env()
    try:
        count = maybe_enrich_budgets(overlay)
        return bool(count)
    except Exception:
        log.exception("budget line enrich failed")
        return False


def cold_start_overviews(overlay_dir: Path | None = None) -> bool:
    """Write missing fact sheets, and any from a prior month. No FAA fetch."""
    overlay = overlay_dir or overlay_dir_from_env()
    if not overlay.is_dir():
        return False
    return refresh_overviews(overlay) > 0


def _rebuild_site() -> None:
    site_dir = os.environ.get("APTPLANS_SITE", "").strip()
    if not site_dir:
        return
    builder = ROOT / "site" / "build.py"
    subprocess.run(
        [sys.executable, str(builder), "--out", site_dir],
        check=False,
        cwd=str(ROOT),
    )


def idle_seconds() -> float:
    raw = os.environ.get("APTPLANS_WORKER_IDLE_SEC", "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_IDLE_SEC


def intake_idle_seconds() -> float:
    raw = os.environ.get("APTPLANS_INTAKE_IDLE_SEC", "").strip()
    if raw:
        try:
            return max(60.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_INTAKE_SEC


def discovery_idle_seconds() -> float:
    raw = os.environ.get("APTPLANS_DISCOVERY_IDLE_SEC", "").strip()
    if raw:
        try:
            return max(3600.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_DISCOVERY_SEC


def run_loop(
    *,
    process=None,
    sleep=time.sleep,
    idle: float | None = None,
    intake_idle: float | None = None,
    discovery_idle: float | None = None,
    job_pause: float | None = None,
    now=time.monotonic,
) -> None:
    """Drain the queue serially. One airport batch at a time by default."""
    pause = idle_seconds() if idle is None else idle
    intake_every = intake_idle_seconds() if intake_idle is None else intake_idle
    discovery_every = discovery_idle_seconds() if discovery_idle is None else discovery_idle
    between_jobs = job_pause_seconds() if job_pause is None else job_pause
    slots = airport_concurrency()
    if slots > 1:
        log.warning(
            "APTPLANS_AIRPORT_CONCURRENCY=%s; worker still drains one job at a time",
            slots,
        )

    last_intake: float | None = None
    last_discovery: float | None = None
    busy = False
    while True:
        try:
            if process is not None:
                worked = process()
            else:
                worked = process_next(pull_intake=False, pull_discovery=False)
                if not worked:
                    due_intake = last_intake is None or (now() - last_intake) >= intake_every
                    if due_intake:
                        worked = process_next(pull_intake=True, pull_discovery=False)
                        last_intake = now()
                if not worked:
                    due_discovery = last_discovery is None or (now() - last_discovery) >= discovery_every
                    if due_discovery:
                        worked = process_next(pull_intake=False, pull_discovery=True)
                        last_discovery = now()
        except JobRetry as exc:
            delay = exc.delay_seconds()
            log.exception("job failed; retry in %.0fs", delay)
            sleep(delay)
            continue
        except Exception:
            log.exception("job failed; waiting before retry")
            worked = False
        if worked:
            busy = True
            if between_jobs > 0:
                sleep(between_jobs)
            continue
        if busy:
            log.info("queue empty; next poll in %.0fs", pause)
            busy = False
        sleep(pause)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log.info(
        "worker drain host=%s model=%s idle_sec=%s intake_sec=%s discovery_sec=%s airport_slots=%s job_pause_sec=%s",
        os.environ.get("OLLAMA_HOST", ""),
        os.environ.get("OLLAMA_MODEL", ""),
        idle_seconds(),
        intake_idle_seconds(),
        discovery_idle_seconds(),
        airport_concurrency(),
        job_pause_seconds(),
    )
    attach_jsonl_handler(logging.getLogger("aptplans"), name="worker")
    rebuilt = False
    try:
        if cold_start_overlays(post_json=post_json):
            rebuilt = True
    except Exception:
        log.exception("FAA overlay fetch failed; worker stays up")
    try:
        if cold_start_grant_spend():
            rebuilt = True
    except Exception:
        log.exception("grant spend classification failed; worker stays up")
    try:
        if cold_start_budget_lines():
            rebuilt = True
    except Exception:
        log.exception("budget line classification failed; worker stays up")
    try:
        if cold_start_overviews():
            rebuilt = True
    except Exception:
        log.exception("overview refresh failed; worker stays up")
    if rebuilt:
        _rebuild_site()
    try:
        boot_sync()
    except Exception:
        log.exception("search index sync failed; worker stays up")
    overlay = overlay_dir_from_env()
    queue = queue_dir_from_env()
    try:
        refresh_public_site(overlay, queue, ROOT / "catalog")
    except Exception:
        log.exception("initial site refresh failed; worker stays up")
    run_loop()


if __name__ == "__main__":
    main()
