"""Probe official URLs. Mark live, moved, or dead. Try mirrors, then Wayback, to rediscover."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse
import json
import logging
import os
import time
from pathlib import Path

from catalog.models import Document
from catalog.seed import seed_catalog
from catalog.store import write_overlay_update
from pipeline.fetch import fetch_bytes, fetch_meta
from pipeline.lock import worker_lock
from pipeline.queue import JobQueue, QueueJob
from pipeline.refresh import PAUSE_SECONDS, ROOT, overlay_dir_from_env

log = logging.getLogger("aptplans.check")

DEAD_HTTP = {404, 410, 451}
LIVE_HTTP = {200, 203, 204, 206, 304}
HEAD_UNSUPPORTED = {400, 405, 501}
LIVE_RECHECK = timedelta(days=7)
DEAD_RECHECK = timedelta(days=30)
DEFAULT_LIMIT = 40
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"


@dataclass(frozen=True)
class ProbeResult:
    status: str
    http_status: int | None = None
    final_url: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class CheckOutcome:
    document_id: str
    status: str
    updates: dict
    fetch_url: str | None = None
    wayback_url: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def same_resource(left: str, right: str) -> bool:
    a = urlparse(left)
    b = urlparse(right)
    path_a = (a.path or "/").rstrip("/") or "/"
    path_b = (b.path or "/").rstrip("/") or "/"
    return a.scheme == b.scheme and a.netloc.lower() == b.netloc.lower() and path_a == path_b


def is_http_url(url: str | None) -> bool:
    if not url:
        return False
    return urlparse(url).scheme in {"http", "https"}


def is_due(document: Document, now: datetime | None = None) -> bool:
    if not is_http_url(document.source_url):
        return False
    checked = _parse_at(document.source_retrieved_at)
    if checked is None:
        return True
    now = now or _utc_now()
    window = DEAD_RECHECK if document.source_status == "dead" else LIVE_RECHECK
    return now - checked >= window


def due_documents(documents: list[Document], now: datetime | None = None) -> list[Document]:
    now = now or _utc_now()
    due = [document for document in documents if is_due(document, now)]
    due.sort(key=lambda document: (document.source_retrieved_at or "", document.id))
    return due


def probe_url(url: str, meta_fn=fetch_meta) -> ProbeResult:
    """HEAD first. GET range only when HEAD is unsupported. 5xx is an error, not dead."""
    if not is_http_url(url) and not url.startswith("file:"):
        return ProbeResult("error", reason="unsupported scheme")
    try:
        status, final = meta_fn(url, method="HEAD")
        if status in HEAD_UNSUPPORTED:
            status, final = meta_fn(url, method="GET")
    except PermissionError as exc:
        return ProbeResult("error", reason=str(exc))
    except (OSError, TimeoutError, ValueError) as exc:
        return ProbeResult("error", reason=str(exc))
    if status in DEAD_HTTP:
        return ProbeResult("dead", http_status=status, final_url=final, reason=f"HTTP {status}")
    if status in LIVE_HTTP or 200 <= status < 300:
        kind = "live" if same_resource(url, final) else "moved"
        return ProbeResult(kind, http_status=status, final_url=final)
    if status >= 500:
        return ProbeResult("error", http_status=status, final_url=final, reason=f"HTTP {status}")
    return ProbeResult("error", http_status=status, final_url=final, reason=f"HTTP {status}")


def wayback_capture(url: str, fetch_fn=None) -> str | None:
    """Latest 200 capture. Default fetch is origin-only (`APTPLANS_WAYBACK=1`)."""
    if fetch_fn is None:
        if os.environ.get("APTPLANS_WAYBACK") != "1":
            return None
        fetch_fn = fetch_bytes
    query = (
        f"{WAYBACK_CDX}?url={quote(url, safe='')}&output=json"
        "&fl=timestamp,original,statuscode&filter=statuscode:200&limit=1"
    )
    try:
        data, status = fetch_fn(query, timeout=20)
    except Exception:
        log.info("wayback cdx failed for %s", url)
        return None
    if int(status) >= 400 or not data:
        return None
    try:
        rows = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    timestamp, original, *_rest = rows[1]
    if not timestamp or not original:
        return None
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def overlay_for_probe(document: Document, result: ProbeResult, checked_at: str) -> dict:
    updates = {
        "source_retrieved_at": checked_at,
    }
    if result.status == "error":
        return updates
    updates["source_status"] = result.status
    if result.status == "live":
        if document.content_sha256:
            updates["completeness"] = "complete"
        return updates
    if result.status == "moved" and result.final_url and not same_resource(document.source_url, result.final_url):
        mirrors = list(document.mirrors)
        if document.source_url not in mirrors:
            mirrors.append(document.source_url)
        updates["source_url"] = result.final_url
        updates["mirrors"] = mirrors
        return updates
    if result.status == "dead":
        if document.content_sha256:
            updates["completeness"] = "preserved_only"
        elif document.completeness == "link_only":
            updates["completeness"] = "missing"
        return updates
    return updates


def live_mirrors(document: Document, probe_fn=probe_url) -> list[str]:
    found: list[str] = []
    for url in document.mirrors:
        if not is_http_url(url) or same_resource(url, document.source_url):
            continue
        result = probe_fn(url)
        if result.status in {"live", "moved"}:
            found.append(result.final_url or url)
    return found


def check_document(
    document: Document,
    *,
    checked_at: str | None = None,
    probe_fn=None,
    wayback_fn=None,
) -> CheckOutcome:
    probe = probe_fn or probe_url
    wayback = wayback_fn or wayback_capture
    result = probe(document.source_url)
    stamp = checked_at or _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    updates = overlay_for_probe(document, result, stamp)
    fetch_url = None
    wayback_url = None
    if result.status == "moved" and result.final_url:
        fetch_url = result.final_url
    if result.status == "dead":
        mirrors = live_mirrors(document, probe_fn=probe)
        if mirrors:
            fetch_url = mirrors[0]
        elif not document.content_sha256:
            wayback_url = wayback(document.source_url)
            if wayback_url:
                fetch_url = wayback_url
    log.info(
        "check %s %s -> %s",
        document.id,
        document.source_url,
        result.status,
    )
    return CheckOutcome(
        document_id=document.id,
        status=result.status,
        updates=updates,
        fetch_url=fetch_url,
        wayback_url=wayback_url,
    )


def _enqueue_fetch(queue: JobQueue, document: Document, url: str) -> None:
    queue.enqueue(
        QueueJob(
            kind="fetch",
            document_id=document.id,
            source_url=url,
            airport_lid=document.airport_lid,
            state=document.state,
            suggested_kind=document.kind,
        )
    )


def apply_outcome(
    outcome: CheckOutcome,
    document: Document,
    overlay_dir: Path,
    queue: JobQueue | None,
) -> None:
    write_overlay_update(overlay_dir, document.id, outcome.updates)
    if outcome.fetch_url and queue is not None:
        _enqueue_fetch(queue, document, outcome.fetch_url)


def run_check_pass(
    *,
    overlay_dir: Path,
    catalog_root: Path,
    queue_dir: Path | None = None,
    limit: int | None = None,
    probe_fn=None,
    wayback_fn=None,
    sleep=time.sleep,
    pause_seconds: float = PAUSE_SECONDS,
) -> int:
    catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
    cap = limit if limit is not None else int(os.environ.get("APTPLANS_CHECK_LIMIT", DEFAULT_LIMIT))
    queue = JobQueue(queue_dir) if queue_dir is not None else None
    probe = probe_fn or probe_url
    wayback = wayback_fn or wayback_capture
    checked = 0
    last_host = ""
    for document in due_documents(catalog.documents):
        if checked >= cap:
            break
        host = urlparse(document.source_url).netloc
        if last_host and host != last_host and pause_seconds:
            sleep(pause_seconds)
        last_host = host
        outcome = check_document(document, probe_fn=probe, wayback_fn=wayback)
        apply_outcome(outcome, document, overlay_dir, queue)
        checked += 1
    log.info("checked %s official URLs", checked)
    return checked


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    overlay = overlay_dir_from_env()
    queue = Path(os.environ.get("APTPLANS_QUEUE", ROOT / "data" / "queue"))
    catalog_root = ROOT / "catalog"
    with worker_lock(queue):
        count = run_check_pass(overlay_dir=overlay, catalog_root=catalog_root, queue_dir=queue)
    if count:
        from pipeline.site_build import enqueue_site_build

        enqueue_site_build(queue)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
