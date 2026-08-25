"""Worker tempo: airport concurrency slots and pause between jobs."""

from __future__ import annotations

import os

DEFAULT_AIRPORT_CONCURRENCY = 1
DEFAULT_JOB_PAUSE_SEC = 2.0


def airport_concurrency() -> int:
    """Maximum distinct airports with active leases. Default 1."""
    raw = os.environ.get("APTPLANS_AIRPORT_CONCURRENCY", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_AIRPORT_CONCURRENCY


def job_pause_seconds() -> float:
    """Sleep after each successful job to pace search and LLM load."""
    raw = os.environ.get("APTPLANS_JOB_PAUSE_SEC", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_JOB_PAUSE_SEC
