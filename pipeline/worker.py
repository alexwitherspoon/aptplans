"""Compose worker. Drains the on-disk queue with airport-scoped concurrency."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from pipeline.boot_jobs import enqueue_boot_jobs
from pipeline.pace import airport_concurrency, job_pause_seconds
from pipeline.queue import JobRetry
from pipeline.run_once import _refresh_pipeline, process_next
from pipeline.refresh import ROOT, overlay_dir_from_env
from pipeline.service_log import attach_jsonl_handler
from pipeline.status import queue_dir_from_env

log = logging.getLogger("aptplans.worker")

DEFAULT_IDLE_SEC = 60.0
DEFAULT_INTAKE_SEC = 3600.0
DEFAULT_DISCOVERY_SEC = 86400.0


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
    overlay = overlay_dir_from_env()
    queue = queue_dir_from_env()
    try:
        _refresh_pipeline(overlay, queue, ROOT / "catalog")
    except Exception:
        log.exception("pipeline snapshot failed; worker stays up")
    try:
        enqueued = enqueue_boot_jobs(queue)
        if enqueued:
            log.info("boot jobs enqueued: %s", ",".join(enqueued))
    except Exception:
        log.exception("boot job enqueue failed; worker stays up")
    run_loop()


if __name__ == "__main__":
    main()
