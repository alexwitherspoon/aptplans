"""Origin health snapshot for the private review API. Disk only. No docker.sock."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

from catalog.store import load_overviews_overlay, load_overlay
from pipeline.brief import overview_is_stale
from pipeline.meter import ledger_summary
from pipeline.outcomes import outcome_stats
from pipeline.refresh import overlay_airports_path, overlay_grants_path, should_refresh
from pipeline.reject import live_rejects, purge_expired, reject_dir as reject_dir_from_env
from pipeline.service_log import DEFAULT_TAIL, MAX_TAIL, logs_dir_from_env, tail_jsonl, worker_log_path


def queue_dir_from_env(override: Path | None = None) -> Path:
    if override is not None:
        return override
    raw = os.environ.get("APTPLANS_QUEUE", "").strip()
    if raw:
        return Path(raw)
    overlay = os.environ.get("APTPLANS_CATALOG_OVERLAY", "").strip()
    if overlay:
        return Path(overlay).parent / "queue"
    return Path(__file__).resolve().parents[1] / "data" / "queue"


def _mtime(path: Path) -> dict:
    if not path.is_file():
        return {"name": path.name, "present": False}
    stat = path.stat()
    stamp = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "name": path.name,
        "present": True,
        "bytes": stat.st_size,
        "mtime": stamp,
        "stale_month": should_refresh(path),
    }


def _jsonl_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _queue_counts(queue_dir: Path) -> dict[str, int]:
    counts = {}
    for name in ("pending", "active", "done"):
        folder = queue_dir / name
        if not folder.is_dir():
            counts[name] = 0
            continue
        counts[name] = sum(1 for path in folder.glob("*.json"))
    return counts


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


def system_status(
    overlay_dir: Path,
    *,
    queue_dir: Path | None = None,
    reject_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> dict:
    """Queue depth, overlay freshness, scoring mix, fact sheets, search spend."""
    queue = queue_dir or queue_dir_from_env()
    rejects = reject_dir or reject_dir_from_env()
    logs = logs_dir or logs_dir_from_env()
    airports = overlay_airports_path(overlay_dir)
    grants = overlay_grants_path(overlay_dir)
    overviews_path = overlay_dir / "overviews.jsonl"
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overlay": {
            "airports": {**_mtime(airports), "n": _jsonl_count(airports)},
            "grants": {**_mtime(grants), "n": _jsonl_count(grants)},
            "overviews": {**_mtime(overviews_path), **_overview_stats(overlay_dir)},
            "documents": _document_stats(overlay_dir),
            "search_meter": ledger_summary(overlay_dir),
        },
        "queue": _queue_counts(queue),
        "outcomes": outcome_stats(overlay_dir=overlay_dir),
        "rejects": {"n": _reject_count(rejects)},
        "logs": {"worker_lines": _jsonl_count(worker_log_path(logs))},
    }


def service_logs(
    overlay_dir: Path,
    *,
    logs_dir: Path | None = None,
    n: int = DEFAULT_TAIL,
) -> dict:
    from pipeline.outcomes import compact_outcome, load_outcomes

    count = max(1, min(int(n), MAX_TAIL))
    logs = logs_dir or logs_dir_from_env()
    outcomes = [compact_outcome(row) for row in load_outcomes(overlay_dir)[-count:]]
    worker = tail_jsonl(worker_log_path(logs), count)
    return {
        "n": count,
        "worker": worker,
        "outcomes": outcomes,
    }
