"""One serial pipeline pass. Invoked by systemd via `compose exec worker`."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
import logging
import os
import subprocess
import sys

from catalog.models import Airport
from catalog.seed import seed_catalog
from catalog.store import completeness_for_airport, upsert_airport_overlay, write_overlay_update
from pipeline.fetch import fetch_bytes, post_json
from pipeline.files import store_bytes
from pipeline.gates import evaluate_file, filename_from_url, intake_status
from pipeline.github import GitHubIntake, github_from_env
from pipeline.intake import IntakeHint, hint_can_queue, parse_issue_body, resolve_intake
from pipeline.queue import JobQueue, QueueJob
from pipeline.refresh_airports import maybe_refresh

log = logging.getLogger("aptplans.job")

ROOT = Path(__file__).resolve().parents[1]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paths(
    queue_dir: Path | None,
    files_dir: Path | None,
    overlay_dir: Path | None,
    catalog_root: Path | None,
) -> tuple[Path, Path, Path, Path]:
    return (
        queue_dir or Path(os.environ.get("APTPLANS_QUEUE", ROOT / "data" / "queue")),
        files_dir or Path(os.environ.get("APTPLANS_FILES", ROOT / "data" / "files")),
        overlay_dir
        or Path(os.environ.get("APTPLANS_CATALOG_OVERLAY", ROOT / "data" / "catalog")),
        catalog_root or (ROOT / "catalog"),
    )


def _reply(job: QueueJob, status: str, hint: IntakeHint | None) -> None:
    if job.issue_number is None:
        return
    client = github_from_env()
    if client is None:
        log.info("intake token unset; skip GitHub comment on issue %s", job.issue_number)
        return
    if hint is None:
        hint = IntakeHint(
            report_type=job.report_type or "add",
            kind=job.suggested_kind or "other",
            airport_lid=job.airport_lid,
            state=job.state,
            source_url=job.source_url,
            notes="",
        )
    GitHubIntake(client).apply(job.issue_number, resolve_intake(hint, status))


def _pull_intake(queue: JobQueue) -> QueueJob | None:
    client = github_from_env()
    if client is None:
        return None
    issues = client.open_intake_issues()
    if not issues:
        return None
    issue = issues[0]
    hint = parse_issue_body(issue.body)
    if not hint_can_queue(hint):
        GitHubIntake(client).apply(issue.number, resolve_intake(hint, "needs_human"))
        return None
    job = QueueJob(
        kind="fetch",
        document_id=None,
        source_url=hint.source_url,
        airport_lid=hint.airport_lid,
        state=hint.state,
        issue_number=issue.number,
        report_type=hint.report_type,
        suggested_kind=hint.kind,
    )
    queue.enqueue(job)
    return queue.claim()


def _maybe_rebuild_site() -> None:
    site_dir = os.environ.get("APTPLANS_SITE", "").strip()
    if not site_dir:
        return
    builder = ROOT / "site" / "build.py"
    subprocess.run(
        [sys.executable, str(builder), "--out", site_dir],
        check=True,
        cwd=str(ROOT),
    )


def process_fetch(
    job: QueueJob,
    files_dir: Path,
    overlay_dir: Path,
    catalog_root: Path,
) -> str:
    if not job.source_url:
        _reply(job, "needs_human", None)
        return "needs_human"
    try:
        data, _status = fetch_bytes(job.source_url)
    except HTTPError as exc:
        status = "dead" if exc.code in {404, 410} else "needs_human"
        log.info("fetch HTTP %s %s -> %s", exc.code, job.source_url, status)
        _reply(job, status, None)
        return status
    except (URLError, OSError, TimeoutError, ValueError) as exc:
        log.info("fetch failed %s: %s", job.source_url, exc)
        _reply(job, "needs_human", None)
        return "needs_human"

    filename = filename_from_url(job.source_url)
    status = intake_status(evaluate_file(job.source_url, filename, data))
    if status:
        _reply(job, status, None)
        return status

    stored = store_bytes(data, files_dir)
    catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
    if job.airport_lid and job.airport_lid not in catalog.airports_by_lid:
        upsert_airport_overlay(
            overlay_dir,
            Airport(
                lid=job.airport_lid,
                name=job.airport_lid,
                city="",
                state=job.state or "",
                admitted=True,
                sources=["intake"],
            ),
        )
    document_id = job.document_id
    if document_id is None:
        for document in catalog.documents:
            if document.source_url == job.source_url:
                document_id = document.id
                break
        if document_id is None:
            lid = (job.airport_lid or "doc").lower()
            document_id = f"{lid}-{stored.sha256[:12]}"
    previous = catalog.documents_by_id.get(document_id)
    updates = {
        "id": document_id,
        "kind": (previous.kind if previous else job.suggested_kind) or "other",
        "airport_lid": job.airport_lid or (previous.airport_lid if previous else None),
        "state": (previous.state if previous else None) or job.state,
        "title": previous.title if previous else filename,
        "edition": previous.edition if previous else None,
        "source_url": job.source_url,
        "source_retrieved_at": _utc_now(),
        "source_status": "live",
        "content_sha256": stored.sha256,
        "preserved_url": f"/files/{stored.sha256}.pdf",
        "completeness": "complete",
        "review_status": "auto_pass",
        "license_or_rights": previous.license_or_rights if previous else "public_record",
    }
    write_overlay_update(overlay_dir, document_id, updates)
    _reply(job, "preserved", None)
    log.info(
        "preserved %s sha256=%s airport=%s",
        document_id,
        stored.sha256,
        job.airport_lid,
    )
    return "preserved"


def run_once(
    queue_dir: Path | None = None,
    files_dir: Path | None = None,
    overlay_dir: Path | None = None,
    catalog_root: Path | None = None,
) -> int:
    queue_dir, files_dir, overlay_dir, catalog_root = _paths(
        queue_dir, files_dir, overlay_dir, catalog_root
    )
    if os.environ.get("APTPLANS_REFRESH_AIRPORTS") == "1":
        maybe_refresh(overlay_dir, post_json=post_json)
    queue = JobQueue(queue_dir)
    job = queue.claim()
    if job is None:
        job = _pull_intake(queue)
    if job is None:
        log.info("no catalog jobs yet; worker is idle")
        return 0
    if job.kind != "fetch":
        log.info("skip unsupported job kind %s", job.kind)
        queue.complete()
        return 0
    status = process_fetch(job, files_dir, overlay_dir, catalog_root)
    queue.complete()
    if status == "preserved":
        _maybe_rebuild_site()
        catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
        if job.airport_lid:
            log.info(
                "airport %s completeness=%s",
                job.airport_lid,
                completeness_for_airport(catalog, job.airport_lid),
            )
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
