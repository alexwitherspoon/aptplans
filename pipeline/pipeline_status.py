"""Per-airport pipeline touch state and a public queue snapshot. Not a publish."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import json

from catalog.models import AIRPORT_COVERAGE_STAGES, Document, visible_on_site
from catalog.seed import seed_catalog
from catalog.store import has_verified_plans, load_overlay
from pipeline.queue import JobQueue, QueueJob
from pipeline.refresh import ROOT, overlay_dir_from_env
from pipeline.search_scope import parse_search_states, scoped_overlay_airports

STATUS_NAME = "pipeline_status.json"
PUBLIC_NAME = "pipeline.json"
DISCOVERY_CURSOR_NAME = "discovery_cursor.json"
LAST_COMPLETED_NAME = "last_completed.json"

STAGES = AIRPORT_COVERAGE_STAGES

STAGE_LABELS = {
    "untouched": "Not searched yet",
    "searched": "Searched",
    "explored": "Hub explored",
    "snapshot_pending": "Awaiting review",
    "published": "Published",
    "no_plan_found": "No plan found yet",
}

STAGE_DESCRIPTIONS = {
    "untouched": "Listed from FAA records. Discovery has not run for this airport yet.",
    "searched": "Web search ran. Hubs and PDF candidates may be queued next.",
    "explored": "Hub pages were fetched. Plan-shaped links are candidates for snapshot.",
    "snapshot_pending": "A file was preserved on origin and is waiting for human or model review.",
    "published": "A master plan or Airport Layout Plan is listed on the public site.",
    "no_plan_found": "Discovery and explore ran; no official plan or ALP is listed yet.",
}

STAGE_DISPLAY_ORDER = (
    "untouched",
    "searched",
    "explored",
    "snapshot_pending",
    "no_plan_found",
    "published",
)

BANNER_TEXT = {
    "untouched": (
        "Not reviewed yet. FAA identity and federal grants may appear below, but AptPlans "
        "has not searched this airport for master plans or Airport Layout Plans. "
        "Empty plan sections do not mean none exist."
    ),
    "searched": "AptPlans ran a web search for official plans on {date}. Nothing is published yet.",
    "explored": "AptPlans explored likely hub pages on {date}. Nothing is published yet.",
    "snapshot_pending": (
        "AptPlans preserved one or more files that are awaiting review. "
        "They are not published on this site yet."
    ),
    "published": None,
    "no_plan_found": (
        "AptPlans searched and explored this airport on {date}. "
        "No official master plan or Airport Layout Plan is listed yet."
    ),
}

PLAN_PANEL_EMPTY = {
    "untouched": {
        "alp": (
            "AptPlans has not searched this airport yet. "
            "Whether an Airport Layout Plan exists is unknown."
        ),
        "master_plan": (
            "AptPlans has not searched this airport yet. "
            "Whether a master plan exists is unknown."
        ),
    },
    "searched": {
        "alp": "A web search ran; no Airport Layout Plan is published here yet.",
        "master_plan": "A web search ran; no master plan is published here yet.",
    },
    "explored": {
        "alp": "Hub pages were explored; no Airport Layout Plan is published here yet.",
        "master_plan": "Hub pages were explored; no master plan is published here yet.",
    },
    "snapshot_pending": {
        "alp": "Files are preserved and awaiting review. Nothing is published here yet.",
        "master_plan": "Files are preserved and awaiting review. Nothing is published here yet.",
    },
    "no_plan_found": {
        "alp": "AptPlans searched this airport. No official Airport Layout Plan is listed yet.",
        "master_plan": "AptPlans searched this airport. No official master plan is listed yet.",
    },
}

PLAN_KINDS = frozenset({"master_plan", "alp", "chapter", "other"})


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_lid(lid: str | None) -> str:
    return (lid or "").strip().upper()


def status_path(overlay_dir: Path | None = None) -> Path:
    return overlay_dir_from_env(overlay_dir) / STATUS_NAME


def public_path(overlay_dir: Path | None = None) -> Path:
    return overlay_dir_from_env(overlay_dir) / PUBLIC_NAME


def load_status(overlay_dir: Path | None = None) -> dict[str, dict]:
    path = status_path(overlay_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        _normalize_lid(lid): dict(row)
        for lid, row in payload.items()
        if _normalize_lid(lid) and isinstance(row, dict)
    }


def save_status(overlay_dir: Path, rows: dict[str, dict]) -> None:
    path = status_path(overlay_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {lid: rows[lid] for lid in sorted(rows)}
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def _host(url: str | None) -> str:
    if not url or not url.startswith("http"):
        return ""
    return urlparse(url).netloc or ""


def record_queue_completion(queue_dir: Path, job: QueueJob, status: str, *, at: str | None = None) -> None:
    """Remember the last queue job that finished (not per-airport touch history)."""
    stamp = at or utc_now()
    path = queue_dir / LAST_COMPLETED_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": stamp,
        "kind": job.kind,
        "airport_lid": _normalize_lid(job.airport_lid) or None,
        "status": status,
        "job_id": job.id,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_last_queue_completion(queue_dir: Path) -> dict:
    path = queue_dir / LAST_COMPLETED_NAME
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def record_job(
    overlay_dir: Path,
    job: QueueJob,
    status: str,
    *,
    at: str | None = None,
) -> None:
    """Update per-airport timestamps after a worker job."""
    lid = _normalize_lid(job.airport_lid)
    if not lid:
        return
    stamp = at or utc_now()
    rows = load_status(overlay_dir)
    row = dict(rows.get(lid) or {})
    row["last_job_at"] = stamp
    row["last_job_kind"] = job.kind
    row["last_job_status"] = status
    if job.kind == "explore":
        row["explored_at"] = stamp
    elif job.kind == "fetch" and status == "preserved":
        row["snapshot_at"] = stamp
    elif job.kind == "vet":
        row["vetted_at"] = stamp
    rows[lid] = row
    save_status(overlay_dir, rows)


def record_discovery(
    overlay_dir: Path,
    lids: list[str],
    *,
    at: str | None = None,
) -> None:
    stamp = at or utc_now()
    rows = load_status(overlay_dir)
    for raw in lids:
        lid = _normalize_lid(raw)
        if not lid:
            continue
        row = dict(rows.get(lid) or {})
        row["discovery_at"] = stamp
        row["last_job_at"] = stamp
        row["last_job_kind"] = "discovery"
        row["last_job_status"] = "searched"
        rows[lid] = row
    save_status(overlay_dir, rows)


def _pending_plan_docs(overlay_dir: Path, lid: str) -> list[dict]:
    overlay = load_overlay(overlay_dir)
    pending = []
    for row in overlay.values():
        if _normalize_lid(row.get("airport_lid")) != lid:
            continue
        if row.get("completeness") != "complete":
            continue
        if (row.get("review_status") or "pending") != "pending":
            continue
        if row.get("kind") not in PLAN_KINDS:
            continue
        pending.append(row)
    return pending


def coverage_stage(
    lid: str,
    *,
    overlay_dir: Path | None = None,
    catalog_root: Path | None = None,
    status_rows: dict[str, dict] | None = None,
) -> str:
    """Derive public coverage stage from overlay, catalog, and touch history."""
    lid = _normalize_lid(lid)
    catalog = seed_catalog(catalog_root or ROOT / "catalog", overlay_dir=overlay_dir)
    docs = catalog.documents_for_airport(lid)
    rows = status_rows if status_rows is not None else load_status(overlay_dir)
    row = rows.get(lid) or {}
    if has_verified_plans(catalog, lid):
        return "published"
    touched = bool(
        row.get("discovery_at") or row.get("explored_at") or row.get("snapshot_at")
    )
    if _pending_plan_docs(overlay_dir or overlay_dir_from_env(), lid):
        return "snapshot_pending"
    if any(visible_on_site(doc) and doc.kind in {"master_plan", "alp"} for doc in docs):
        if touched:
            return "published"
        return "untouched"
    if row.get("explored_at") or row.get("snapshot_at"):
        if row.get("last_job_status") in {"dead", "not_plan", "ssi"} and not row.get("snapshot_at"):
            return "no_plan_found"
        return "explored"
    if row.get("discovery_at"):
        return "searched"
    return "untouched"


def coverage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, STAGE_LABELS["untouched"])


def coverage_banner(stage: str, row: dict | None = None) -> str | None:
    template = BANNER_TEXT.get(stage)
    if not template:
        return None
    row = row or {}
    date = (row.get("last_job_at") or row.get("discovery_at") or "")[:10]
    if "{date}" in template:
        return template.format(date=date or "a recent pass")
    return template


def coverage_banner_class(stage: str) -> str:
    if stage == "untouched":
        return "coverage-banner--unreviewed"
    if stage == "no_plan_found":
        return "coverage-banner--searched"
    if stage in {"searched", "explored"}:
        return "coverage-banner--in-progress"
    if stage == "snapshot_pending":
        return "coverage-banner--pending"
    return ""


def plan_panel_empty(stage: str, kind: str, *, alp_listed: bool = False) -> str:
    """Empty-state copy for plan panels, keyed by pipeline coverage stage."""
    if kind == "master_plan" and alp_listed and stage not in {"untouched", "published"}:
        return "No master plan is listed yet. An Airport Layout Plan can stand on its own."
    by_stage = PLAN_PANEL_EMPTY.get(stage) or PLAN_PANEL_EMPTY["untouched"]
    return by_stage[kind]


def pending_documents(catalog, lid: str) -> list[Document]:
    """Complete snapshots still in review. Not listed in the public catalog."""
    lid = _normalize_lid(lid)
    rows = []
    for document in catalog.documents_for_airport(lid):
        if document.completeness != "complete":
            continue
        if (document.review_status or "pending") != "pending":
            continue
        if document.kind not in PLAN_KINDS:
            continue
        rows.append(document)
    rows.sort(key=lambda doc: doc.source_retrieved_at or doc.id, reverse=True)
    return rows


def _job_public(job: QueueJob) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "airport_lid": _normalize_lid(job.airport_lid) or None,
        "host": _host(job.source_url),
        "state": job.state,
    }


NEXT_QUEUE_JOBS = 10


def _next_queue_jobs(queue: JobQueue, *, limit: int = NEXT_QUEUE_JOBS) -> list[QueueJob]:
    """Next jobs in the pending queue. Airport jobs dedupe by LID; maintenance jobs keep FIFO."""
    rows: list[QueueJob] = []
    seen_lids: set[str] = set()
    for path in sorted(queue.pending.glob("*.json")):
        try:
            job = QueueJob.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            continue
        lid = _normalize_lid(job.airport_lid)
        if lid:
            if lid in seen_lids:
                continue
            seen_lids.add(lid)
        rows.append(job)
        if len(rows) >= limit:
            break
    return rows


def _queue_snapshot(queue_dir: Path) -> dict:
    queue = JobQueue(queue_dir)
    active_jobs = []
    for path in sorted(queue.active.glob("*.json")):
        try:
            active_jobs.append(QueueJob.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            continue
    pending_jobs = _next_queue_jobs(queue)
    counts = {
        "pending": sum(1 for _ in queue.pending.glob("*.json")),
        "active": sum(1 for _ in queue.active.glob("*.json")),
        "done": sum(1 for _ in queue.done.glob("*.json")),
    }
    return {
        "counts": counts,
        "active": [_job_public(job) for job in active_jobs],
        "next": [_job_public(job) for job in pending_jobs],
    }


def _coverage_stats(
    overlay_dir: Path,
    catalog_root: Path,
    *,
    states: frozenset[str] | None = None,
) -> dict[str, int]:
    rows = load_status(overlay_dir)
    stats = {stage: 0 for stage in STAGES}
    if states is not None:
        lids = [airport.lid for airport in scoped_overlay_airports(overlay_dir, states=states)]
    else:
        catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
        lids = [airport.lid for airport in catalog.airports]
    for lid in lids:
        stage = coverage_stage(
            lid,
            overlay_dir=overlay_dir,
            catalog_root=catalog_root,
            status_rows=rows,
        )
        stats[stage] = stats.get(stage, 0) + 1
    return stats


def _load_discovery_cursor(overlay_dir: Path) -> dict:
    path = overlay_dir / DISCOVERY_CURSOR_NAME
    if not path.is_file():
        return {"index": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"index": 0}
    return payload if isinstance(payload, dict) else {"index": 0}


def build_public_snapshot(
    overlay_dir: Path,
    queue_dir: Path,
    *,
    catalog_root: Path | None = None,
) -> dict:
    """Write pipeline.json for the public site and review API consumers."""
    catalog_root = catalog_root or ROOT / "catalog"
    queue = _queue_snapshot(queue_dir)
    cursor = _load_discovery_cursor(overlay_dir)
    states = parse_search_states()
    scoped = scoped_overlay_airports(overlay_dir, states=states)
    status_rows = load_status(overlay_dir)
    last_job = load_last_queue_completion(queue_dir)
    if not last_job.get("at"):
        last_lid = ""
        last_at = ""
        for lid, row in status_rows.items():
            at = row.get("last_job_at") or ""
            if at >= last_at:
                last_at = at
                last_lid = lid
        last_row = status_rows.get(last_lid) or {}
        last_job = {
            "at": last_at or None,
            "airport_lid": last_lid or None,
            "kind": last_row.get("last_job_kind"),
            "status": last_row.get("last_job_status"),
        }
    payload = {
        "generated_at": utc_now(),
        "queue": queue["counts"],
        "active_jobs": queue["active"],
        "next_jobs": queue["next"],
        "discovery": {
            "cursor_index": int(cursor.get("index") or 0),
            "last_lids": list(cursor.get("last_lids") or []),
            "scope_states": sorted(states) if states else ["*"],
            "scoped_airports": len(scoped),
        },
        "coverage": _coverage_stats(overlay_dir, catalog_root, states=states),
        "last_job": {
            "at": last_job.get("at"),
            "airport_lid": last_job.get("airport_lid"),
            "kind": last_job.get("kind"),
            "status": last_job.get("status"),
        },
    }
    dest = public_path(overlay_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def stage_rows(coverage: dict | None) -> list[dict]:
    """Ordered stage counts for the public About page."""
    rows = coverage if isinstance(coverage, dict) else {}
    return [
        {
            "id": stage,
            "label": STAGE_LABELS[stage],
            "description": STAGE_DESCRIPTIONS[stage],
            "count": int(rows.get(stage) or 0),
        }
        for stage in STAGE_DISPLAY_ORDER
    ]


def load_public_snapshot(overlay_dir: Path | None = None) -> dict:
    path = public_path(overlay_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}
