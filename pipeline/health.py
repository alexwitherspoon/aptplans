"""Unified system health: datasets, services, and pipeline operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from catalog.store import load_overviews_overlay, load_overlay
from pipeline.brief import overview_is_stale
from pipeline.datasets import (
    DATASET_REGISTRY,
    critical_blockers,
    dataset_record,
    reconcile_catalog,
    requirements_met,
)
from pipeline.meter import ledger_summary
from pipeline.outcomes import outcome_stats
from pipeline.queue import ControlQueue, JobQueue
from pipeline.reject import live_rejects, purge_expired, reject_dir as reject_dir_from_env
from pipeline.search_client import live_search_enabled
from pipeline.service_log import worker_log_path


def _jsonl_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _queue_counts(queue_dir: Path) -> dict[str, int]:
    return JobQueue(queue_dir).counts()


def _queue_job_kinds(queue_dir: Path, folder: str) -> list[str]:
    return JobQueue(queue_dir).kinds(folder)


def _document_stats(overlay_dir: Path) -> dict:
    overlay = load_overlay(overlay_dir)
    review: dict[str, int] = {}
    completeness: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for row in overlay.values():
        status = str(row.get("review_status") or "unset")
        review[status] = review.get(status, 0) + 1
        complete = str(row.get("completeness") or "unset")
        completeness[complete] = completeness.get(complete, 0) + 1
        kind = str(row.get("kind") or "unset")
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "n": len(overlay),
        "review_status": review,
        "completeness": completeness,
        "kind": kinds,
    }


def _overview_stats(overlay_dir: Path) -> dict:
    rows = load_overviews_overlay(overlay_dir)
    stale_lids: list[str] = []
    empty_lids: list[str] = []
    for lid, row in rows.items():
        facts = row.get("facts") or []
        if not facts and not row.get("trajectory"):
            empty_lids.append(lid)
        if overview_is_stale(row):
            stale_lids.append(lid)
    stale_lids.sort()
    empty_lids.sort()
    return {
        "n": len(rows),
        "stale": len(stale_lids),
        "empty": len(empty_lids),
        "stale_lids": stale_lids[:200],
        "empty_lids": empty_lids[:200],
    }


def _reject_count(reject_dir: Path) -> int:
    try:
        purge_expired(dest=reject_dir)
    except OSError:
        pass
    try:
        return len(live_rejects(dest=reject_dir))
    except OSError:
        return 0


def _service_worker(queue_dir: Path, logs_dir: Path) -> dict:
    counts = _queue_counts(queue_dir)
    active_kinds = _queue_job_kinds(queue_dir, "active")
    pending_kinds = _queue_job_kinds(queue_dir, "pending")
    worker_lines = _jsonl_count(worker_log_path(logs_dir))
    wedged = counts["active"] > 0 and counts["pending"] == 0 and worker_lines == 0
    ok = not wedged
    detail = None if ok else "active job with no worker log lines"
    return {
        "ok": ok,
        "pending": counts["pending"],
        "active": counts["active"],
        "active_kinds": active_kinds,
        "pending_kinds": pending_kinds,
        "worker_log_lines": worker_lines,
        "detail": detail,
    }


def _service_search(overlay_dir: Path) -> dict:
    meter = ledger_summary(overlay_dir)
    enabled = live_search_enabled()
    ok = enabled
    detail = None if enabled else "live search disabled"
    return {
        "ok": ok,
        "live_search": enabled,
        "meter": meter,
        "detail": detail,
    }


def _service_llm() -> dict:
    try:
        from pipeline.ollama import llm_calls_enabled
    except ImportError:
        return {"ok": False, "enabled": False, "detail": "ollama module unavailable"}
    enabled = llm_calls_enabled()
    return {
        "ok": enabled,
        "enabled": enabled,
        "detail": None if enabled else "LLM calls disabled",
    }


def _legacy_overlay(overlay_dir: Path, catalog: dict) -> dict:
    """Backward-compatible overlay block for existing scripts."""
    datasets = catalog.get("datasets") or {}
    overviews_path = overlay_dir / "overviews.jsonl"
    airports = datasets.get("airports") or {}
    grants = datasets.get("grants") or {}
    return {
        "airports": {
            "name": "airports.jsonl",
            "present": bool(airports.get("available")),
            "bytes": int(airports.get("bytes") or 0),
            "mtime": airports.get("generated_at"),
            "stale_month": airports.get("status") == "stale",
            "n": int(airports.get("rows") or 0),
        },
        "grants": {
            "name": "grants.jsonl",
            "present": bool(grants.get("available")),
            "bytes": int(grants.get("bytes") or 0),
            "mtime": grants.get("generated_at"),
            "stale_month": grants.get("status") == "stale",
            "n": int(grants.get("rows") or 0),
        },
        "overviews": {
            "name": "overviews.jsonl",
            **_overview_stats(overlay_dir),
        },
        "documents": _document_stats(overlay_dir),
        "search_meter": ledger_summary(overlay_dir),
    }


def system_health(
    overlay_dir: Path,
    *,
    queue_dir: Path | None = None,
    reject_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> dict:
    from pipeline.status import logs_dir_from_env, queue_dir_from_env

    queue_path = queue_dir or queue_dir_from_env()
    rejects = reject_dir or reject_dir_from_env()
    logs = logs_dir or logs_dir_from_env()
    queue = JobQueue(queue_path)
    catalog = reconcile_catalog(overlay_dir, queue)
    datasets = {
        name: dataset_record(overlay_dir, name, queue=queue)
        for name in DATASET_REGISTRY
    }
    discovery_ready, discovery_reason = requirements_met("discovery", overlay_dir, queue)
    blockers = critical_blockers(overlay_dir, queue)
    services = {
        "worker": _service_worker(queue_path, logs),
        "search": _service_search(overlay_dir),
        "llm": _service_llm(),
    }
    pipeline = {
        "queue": _queue_counts(queue_path),
        "continuations": queue.continuation_counts(),
        "controls": ControlQueue(queue_path).counts(),
        "ledger_integrity": queue.integrity_check(),
        "outcomes": outcome_stats(overlay_dir=overlay_dir),
        "rejects": {"n": _reject_count(rejects)},
        "logs": {"worker_lines": _jsonl_count(worker_log_path(logs))},
    }
    ok = (
        discovery_ready
        and services["worker"]["ok"]
        and pipeline["ledger_integrity"] == "ok"
    )
    return {
        "ok": ok,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "discovery_ready": discovery_ready,
            "blocking": blockers,
            "detail": discovery_reason if not discovery_ready else None,
        },
        "datasets": datasets,
        "services": services,
        "pipeline": pipeline,
        "overlay": _legacy_overlay(overlay_dir, catalog),
        "queue": pipeline["queue"],
        "outcomes": pipeline["outcomes"],
        "rejects": pipeline["rejects"],
        "logs": pipeline["logs"],
    }
