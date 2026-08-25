"""Overlay dataset catalog: producer-written readiness for workers and review API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os

from pipeline.discovery_priority import funded_first_enabled
from pipeline.queue import JobQueue
from pipeline.refresh import should_refresh

CATALOG_NAME = "datasets.json"
CATALOG_VERSION = 1

STATUS_MISSING = "missing"
STATUS_BUILDING = "building"
STATUS_READY = "ready"
STATUS_STALE = "stale"
STATUS_FAILED = "failed"

USABLE_STATUSES = frozenset({STATUS_READY, STATUS_STALE})


def _domain_store():
    if os.environ.get("APTPLANS_DOMAIN_STORE", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        return None
    from pipeline.domain_store import DomainStore
    from pipeline.status import queue_dir_from_env

    return DomainStore(queue_dir_from_env())


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    path: str | None
    producer: str | None
    depends_on: tuple[str, ...]
    monthly_stale: bool = False


DATASET_REGISTRY: dict[str, DatasetSpec] = {
    "airports": DatasetSpec(
        "airports",
        "airports.jsonl",
        "overlay_refresh",
        (),
        monthly_stale=True,
    ),
    "grants": DatasetSpec(
        "grants",
        "grants.jsonl",
        "overlay_refresh",
        (),
        monthly_stale=True,
    ),
    "budgets": DatasetSpec(
        "budgets",
        "budgets.jsonl",
        "budget_enrich",
        (),
        monthly_stale=False,
    ),
    "overviews": DatasetSpec(
        "overviews",
        "overviews.jsonl",
        "overview_refresh",
        ("airports",),
        monthly_stale=True,
    ),
    "documents": DatasetSpec(
        "documents",
        "documents.jsonl",
        None,
        (),
        monthly_stale=False,
    ),
    "pipeline_snapshot": DatasetSpec(
        "pipeline_snapshot",
        "pipeline.json",
        "pipeline_snapshot",
        (),
        monthly_stale=False,
    ),
    "search_index": DatasetSpec(
        "search_index",
        None,
        "search_sync",
        ("airports",),
        monthly_stale=False,
    ),
}

JOB_REQUIRES: dict[str, tuple[str, ...]] = {
    "grant_spend": ("grants",),
    "overview_refresh": ("airports",),
    "search_sync": ("airports",),
    "link_check": ("documents",),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def catalog_path(overlay_dir: Path) -> Path:
    return overlay_dir / CATALOG_NAME


def _empty_catalog() -> dict:
    return {"version": CATALOG_VERSION, "updated_at": _utc_now(), "datasets": {}}


def load_catalog(overlay_dir: Path) -> dict:
    domain = _domain_store()
    if domain is not None:
        snapshot = domain.snapshot()
        return {
            "version": CATALOG_VERSION,
            "updated_at": snapshot.committed_at,
            "datasets": dict(snapshot.dataset_state),
        }
    path = catalog_path(overlay_dir)
    if not path.is_file():
        return _empty_catalog()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return _empty_catalog()
    if not isinstance(payload, dict):
        return _empty_catalog()
    payload.setdefault("version", CATALOG_VERSION)
    payload.setdefault("datasets", {})
    if not isinstance(payload["datasets"], dict):
        payload["datasets"] = {}
    return payload


def save_catalog(overlay_dir: Path, catalog: dict) -> None:
    domain = _domain_store()
    if domain is not None:
        domain.commit(
            {},
            reason="update dataset readiness",
            dataset_state=dict(catalog.get("datasets") or {}),
        )
        return
    overlay_dir.mkdir(parents=True, exist_ok=True)
    catalog["version"] = CATALOG_VERSION
    catalog["updated_at"] = _utc_now()
    catalog_path(overlay_dir).write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")


def _jsonl_stats(path: Path) -> tuple[int, int]:
    if not path.is_file() or path.stat().st_size == 0:
        return 0, 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0, 0
    rows = sum(1 for line in text.splitlines() if line.strip())
    return rows, path.stat().st_size


def _file_status(spec: DatasetSpec, path: Path) -> tuple[str, bool, int, int]:
    rows, nbytes = _jsonl_stats(path)
    if rows <= 0:
        return STATUS_MISSING, False, rows, nbytes
    stale = spec.monthly_stale and should_refresh(path)
    status = STATUS_STALE if stale else STATUS_READY
    return status, True, rows, nbytes


def _base_record(spec: DatasetSpec) -> dict:
    return {
        "name": spec.name,
        "path": spec.path,
        "producer": spec.producer,
        "depends_on": list(spec.depends_on),
        "status": STATUS_MISSING,
        "available": False,
        "healthy": False,
        "rows": 0,
        "bytes": 0,
        "generated_at": None,
        "generated_by": None,
        "error": None,
    }


def reconcile_dataset(overlay_dir: Path, name: str, catalog: dict | None = None) -> dict:
    """Sync one dataset record from on-disk files without changing building state."""
    spec = DATASET_REGISTRY[name]
    catalog = catalog if catalog is not None else load_catalog(overlay_dir)
    record = dict(catalog["datasets"].get(name) or _base_record(spec))
    if record.get("status") == STATUS_BUILDING:
        return record
    domain = _domain_store()
    if domain is not None and spec.path:
        entity_type = name if name in {
            "airports",
            "grants",
            "budgets",
            "overviews",
            "documents",
        } else None
        rows = len(domain.snapshot().rows(entity_type)) if entity_type else 0
        if rows > 0:
            record.update(
                {
                    "status": record.get("status")
                    if record.get("status") in USABLE_STATUSES
                    else STATUS_READY,
                    "available": True,
                    "healthy": True,
                    "rows": rows,
                    "bytes": 0,
                }
            )
        elif record.get("status") not in {STATUS_FAILED, STATUS_BUILDING}:
            record.update(
                {
                    "status": STATUS_MISSING,
                    "available": False,
                    "healthy": False,
                    "rows": 0,
                    "bytes": 0,
                }
            )
        catalog["datasets"][name] = record
        return record
    if spec.path:
        path = overlay_dir / spec.path
        status, available, rows, nbytes = _file_status(spec, path)
        record.update(
            {
                "status": status,
                "available": available,
                "healthy": available,
                "rows": rows,
                "bytes": nbytes,
            }
        )
        if available and not record.get("generated_at"):
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            record["generated_at"] = stamp
    catalog["datasets"][name] = record
    return record


def reconcile_catalog(overlay_dir: Path, queue: JobQueue | None = None) -> dict:
    """Refresh catalog from disk; clear stuck building when producer is idle."""
    catalog = load_catalog(overlay_dir)
    if _domain_store() is not None and os.environ.get("APTPLANS_CONTROL_WRITER") == "1":
        return catalog
    before = json.dumps(
        catalog.get("datasets") or {},
        sort_keys=True,
        separators=(",", ":"),
    )
    for name, spec in DATASET_REGISTRY.items():
        record = catalog["datasets"].get(name) or _base_record(spec)
        if record.get("status") == STATUS_BUILDING:
            producer = spec.producer or ""
            if not queue or not producer or not queue.has_kind(producer):
                record = reconcile_dataset(overlay_dir, name, catalog)
                catalog["datasets"][name] = record
                continue
        if name not in catalog["datasets"] or record.get("status") != STATUS_BUILDING:
            catalog["datasets"][name] = reconcile_dataset(overlay_dir, name, catalog)
    after = json.dumps(
        catalog.get("datasets") or {},
        sort_keys=True,
        separators=(",", ":"),
    )
    if after != before:
        save_catalog(overlay_dir, catalog)
    return catalog


def dataset_record(overlay_dir: Path, name: str, *, queue: JobQueue | None = None) -> dict:
    catalog = reconcile_catalog(overlay_dir, queue)
    return dict(catalog["datasets"].get(name) or _base_record(DATASET_REGISTRY[name]))


def mark_dataset_building(overlay_dir: Path, name: str, *, job_kind: str) -> dict:
    spec = DATASET_REGISTRY[name]
    catalog = load_catalog(overlay_dir)
    record = dict(catalog["datasets"].get(name) or _base_record(spec))
    record.update(
        {
            "status": STATUS_BUILDING,
            "available": False,
            "healthy": False,
            "generated_by": job_kind,
            "error": None,
        }
    )
    catalog["datasets"][name] = record
    save_catalog(overlay_dir, catalog)
    return record


def mark_dataset_ready(
    overlay_dir: Path,
    name: str,
    *,
    job_kind: str,
    healthy: bool = True,
) -> dict:
    spec = DATASET_REGISTRY[name]
    catalog = load_catalog(overlay_dir)
    record = dict(catalog["datasets"].get(name) or _base_record(spec))
    stamp = _utc_now()
    domain = _domain_store()
    if domain is not None:
        entity_type = name if name in {
            "airports",
            "grants",
            "budgets",
            "overviews",
            "documents",
        } else None
        rows = len(domain.snapshot().rows(entity_type)) if entity_type else int(
            record.get("rows") or 0
        )
        record.update(
            {
                "status": STATUS_READY,
                "available": True,
                "healthy": healthy,
                "rows": rows,
                "bytes": 0,
                "generated_at": stamp,
                "generated_by": job_kind,
                "error": None,
            }
        )
        catalog["datasets"][name] = record
        save_catalog(overlay_dir, catalog)
        return record
    if spec.path:
        path = overlay_dir / spec.path
        status, available, rows, nbytes = _file_status(spec, path)
        record.update(
            {
                "status": status,
                "available": available,
                "healthy": healthy and available,
                "rows": rows,
                "bytes": nbytes,
                "generated_at": stamp,
                "generated_by": job_kind,
                "error": None,
            }
        )
    else:
        record.update(
            {
                "status": STATUS_READY,
                "available": True,
                "healthy": healthy,
                "generated_at": stamp,
                "generated_by": job_kind,
                "error": None,
            }
        )
    catalog["datasets"][name] = record
    save_catalog(overlay_dir, catalog)
    return record


def mark_dataset_failed(
    overlay_dir: Path,
    name: str,
    *,
    job_kind: str,
    error: str,
) -> dict:
    spec = DATASET_REGISTRY[name]
    catalog = load_catalog(overlay_dir)
    record = dict(catalog["datasets"].get(name) or _base_record(spec))
    record.update(
        {
            "status": STATUS_FAILED,
            "available": False,
            "healthy": False,
            "generated_by": job_kind,
            "generated_at": _utc_now(),
            "error": error,
        }
    )
    catalog["datasets"][name] = record
    save_catalog(overlay_dir, catalog)
    return record


def producer_in_flight(queue: JobQueue | None, name: str) -> bool:
    if not queue:
        return False
    spec = DATASET_REGISTRY.get(name)
    if not spec or not spec.producer:
        return False
    return queue.has_kind(spec.producer)


def dataset_usable(
    overlay_dir: Path,
    name: str,
    *,
    queue: JobQueue | None = None,
    allow_stale: bool = True,
) -> tuple[bool, str]:
    record = dataset_record(overlay_dir, name, queue=queue)
    status = record.get("status") or STATUS_MISSING
    if status == STATUS_BUILDING or producer_in_flight(queue, name):
        return False, f"{name}: building"
    if status == STATUS_FAILED:
        return False, f"{name}: failed ({record.get('error') or 'unknown'})"
    if status == STATUS_MISSING or not record.get("available"):
        return False, f"{name}: missing"
    if status == STATUS_STALE and not allow_stale:
        return False, f"{name}: stale"
    if status not in USABLE_STATUSES:
        return False, f"{name}: {status}"
    if not record.get("healthy", True):
        return False, f"{name}: unhealthy"
    return True, ""


def requirements_for_job(job_kind: str) -> tuple[str, ...]:
    if job_kind == "discovery":
        if funded_first_enabled():
            return ("airports", "grants")
        return ("airports",)
    return JOB_REQUIRES.get(job_kind, ())


def dataset_should_refresh(overlay_dir: Path, name: str) -> bool:
    domain = _domain_store()
    if domain is None:
        spec = DATASET_REGISTRY[name]
        return bool(spec.path and should_refresh(overlay_dir / spec.path))
    record = (load_catalog(overlay_dir).get("datasets") or {}).get(name) or {}
    generated = str(record.get("generated_at") or "")
    if record.get("status") not in USABLE_STATUSES or not generated:
        return True
    try:
        stamp = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    except ValueError:
        return True
    now = datetime.now(timezone.utc)
    return (stamp.year, stamp.month) != (now.year, now.month)


def requirements_met(
    job_kind: str,
    overlay_dir: Path,
    queue: JobQueue | None = None,
    *,
    allow_stale: bool = True,
) -> tuple[bool, str]:
    if queue and job_kind in {"discovery", "grant_spend"} and queue.has_kind("overlay_refresh"):
        return False, "overlay_refresh in flight"
    for dep in requirements_for_job(job_kind):
        for upstream in DATASET_REGISTRY[dep].depends_on:
            ok, reason = dataset_usable(
                overlay_dir,
                upstream,
                queue=queue,
                allow_stale=allow_stale,
            )
            if not ok:
                return False, reason
        ok, reason = dataset_usable(
            overlay_dir,
            dep,
            queue=queue,
            allow_stale=allow_stale,
        )
        if not ok:
            return False, reason
    return True, ""


def critical_blockers(
    overlay_dir: Path,
    queue: JobQueue | None = None,
) -> list[str]:
    blockers: list[str] = []
    ok, reason = requirements_met("discovery", overlay_dir, queue)
    if not ok:
        blockers.append(reason)
    airports_ok, airports_reason = dataset_usable(overlay_dir, "airports", queue=queue)
    if not airports_ok and airports_reason not in blockers:
        blockers.append(airports_reason)
    return blockers
