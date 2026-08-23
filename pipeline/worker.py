"""Compose worker. Drains the on-disk queue with airport-scoped concurrency."""

from __future__ import annotations

import logging
import os
import time

from pipeline.boot_jobs import enqueue_boot_jobs
from pipeline.pace import airport_concurrency, job_pause_seconds
from pipeline.queue import JobRetry
from pipeline.run_once import process_next
from pipeline.service_log import attach_jsonl_handler
from pipeline.status import queue_dir_from_env

log = logging.getLogger("aptplans.worker")

DEFAULT_IDLE_SEC = 60.0


def idle_seconds() -> float:
    raw = os.environ.get("APTPLANS_WORKER_IDLE_SEC", "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_IDLE_SEC


def run_loop(
    *,
    process=None,
    sleep=time.sleep,
    idle: float | None = None,
    job_pause: float | None = None,
) -> None:
    """Drain the queue serially. Periodic maintenance is enqueued by systemd timers."""
    pause = idle_seconds() if idle is None else idle
    between_jobs = job_pause_seconds() if job_pause is None else job_pause
    slots = airport_concurrency()
    if slots > 1:
        log.warning(
            "APTPLANS_AIRPORT_CONCURRENCY=%s; worker still drains one job at a time",
            slots,
        )

    busy = False
    while True:
        try:
            if process is not None:
                worked = process()
            else:
                worked = process_next(pull_intake=False, pull_discovery=False)
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
        "worker drain host=%s model=%s idle_sec=%s airport_slots=%s job_pause_sec=%s",
        os.environ.get("OLLAMA_HOST", ""),
        os.environ.get("OLLAMA_MODEL", ""),
        idle_seconds(),
        airport_concurrency(),
        job_pause_seconds(),
    )
    attach_jsonl_handler(logging.getLogger("aptplans"), name="worker")
    queue = queue_dir_from_env()
    try:
        enqueued = enqueue_boot_jobs(queue)
        if enqueued:
            log.info("boot jobs enqueued: %s", ",".join(enqueued))
    except Exception:
        log.exception("boot job enqueue failed; worker stays up")
    run_loop()


if __name__ == "__main__":
    main()
