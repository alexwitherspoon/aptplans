"""Serial Compose worker. Drains the on-disk queue, one job at a time."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from pipeline.fetch import fetch_bytes, post_json
from pipeline.queue import JobRetry
from pipeline.refresh import ROOT, overlays_need_fetch
from pipeline.refresh_airports import maybe_refresh
from pipeline.run_once import process_next
from pipeline.search import boot_sync

log = logging.getLogger("aptplans.worker")

BOOT_PAUSE_SECONDS = 5.0
DEFAULT_IDLE_SEC = 60.0
DEFAULT_INTAKE_SEC = 3600.0


def cold_start_overlays(
    overlay_dir: Path | None = None,
    *,
    fetch=fetch_bytes,
    sleep=time.sleep,
    pause_before: float = BOOT_PAUSE_SECONDS,
    post_json=None,
) -> bool:
    """Fetch NASR, NPIAS, and AIP grants if overlays are missing or stale. Never force."""
    if os.environ.get("APTPLANS_REFRESH_AIRPORTS") != "1":
        log.info("FAA overlay fetch off (APTPLANS_REFRESH_AIRPORTS unset)")
        return False
    overlay = overlay_dir or Path(
        os.environ.get("APTPLANS_CATALOG_OVERLAY", ROOT / "data" / "catalog")
    )
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


def run_loop(
    *,
    process=None,
    sleep=time.sleep,
    idle: float | None = None,
    intake_idle: float | None = None,
    now=time.monotonic,
) -> None:
    """Concurrency 1: start the next job only after the current one finishes."""
    pause = idle_seconds() if idle is None else idle
    intake_every = intake_idle_seconds() if intake_idle is None else intake_idle
    last_intake: float | None = None
    busy = False
    while True:
        try:
            if process is not None:
                worked = process()
            else:
                worked = process_next(pull_intake=False)
                if not worked:
                    due = last_intake is None or (now() - last_intake) >= intake_every
                    if due:
                        worked = process_next(pull_intake=True)
                        last_intake = now()
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
        "worker drain host=%s model=%s idle_sec=%s intake_sec=%s",
        os.environ.get("OLLAMA_HOST", ""),
        os.environ.get("OLLAMA_MODEL", ""),
        idle_seconds(),
        intake_idle_seconds(),
    )
    try:
        if cold_start_overlays(post_json=post_json):
            _rebuild_site()
    except Exception:
        log.exception("FAA overlay fetch failed; worker stays up")
    try:
        boot_sync()
    except Exception:
        log.exception("search index sync failed; worker stays up")
    run_loop()


if __name__ == "__main__":
    main()
