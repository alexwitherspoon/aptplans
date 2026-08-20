"""One serial pipeline pass. Invoked by systemd via `compose exec worker`."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
import logging
import os
import subprocess
import sys

from catalog.models import (
    Airport,
    ChangeEvent,
    Document,
    find_same_content,
    prior_work_document,
)
from catalog.seed import seed_catalog
from catalog.store import append_change, completeness_for_airport, upsert_airport_overlay, write_overlay_update
from pipeline.check import apply_outcome, check_document
from pipeline.fetch import fetch_bytes
from pipeline.files import store_bytes
from pipeline.gates import evaluate_file, filename_from_url, intake_status
from pipeline.github import GitHubIntake, github_from_env
from pipeline.intake import IntakeHint, hint_can_queue, parse_issue_body, resolve_intake
from pipeline.parse import change_note, content_changed, content_fingerprint
from pipeline.lock import worker_lock
from pipeline.queue import MAX_ATTEMPTS, JobQueue, JobRetry, QueueJob

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


def _intake_job_from_github() -> QueueJob | None:
    """List one open intake issue. No queue lock; caller enqueues under the flock."""
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
    return QueueJob(
        kind="fetch",
        document_id=None,
        source_url=hint.source_url,
        airport_lid=hint.airport_lid,
        state=hint.state,
        issue_number=issue.number,
        report_type=hint.report_type,
        suggested_kind=hint.kind,
    )


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


def _rebuild_if_needed(rebuild: bool) -> None:
    if not rebuild:
        return
    try:
        _maybe_rebuild_site()
    except Exception:
        log.exception("site rebuild failed; catalog overlay already written")


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
    except (URLError, OSError, TimeoutError, ValueError, PermissionError) as exc:
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
    text_sha = None
    images_sha = None
    try:
        text_sha, images_sha = content_fingerprint(data)
    except Exception:
        log.exception("fingerprint failed; preserve still counts")

    document_id = job.document_id
    previous = None
    if document_id is None:
        for document in catalog.documents:
            if document.source_url == job.source_url:
                document_id = document.id
                break
    if document_id is not None:
        previous = catalog.documents_by_id.get(document_id)
    reused_content = False
    if previous is None:
        twin = find_same_content(
            catalog.documents,
            airport_lid=job.airport_lid,
            text_sha256=text_sha,
            images_sha256=images_sha,
        )
        if twin is not None:
            document_id = twin.id
            previous = twin
            reused_content = True
    if document_id is None:
        lid = (job.airport_lid or "doc").lower()
        document_id = f"{lid}-{stored.sha256[:12]}"

    kind = (previous.kind if previous else job.suggested_kind) or "other"
    mirrors = list(previous.mirrors) if previous else []
    source_url = job.source_url
    if reused_content and previous.source_url:
        source_url = previous.source_url
        if job.source_url and job.source_url not in mirrors:
            mirrors.append(job.source_url)
    updates = {
        "id": document_id,
        "kind": kind,
        "airport_lid": job.airport_lid or (previous.airport_lid if previous else None),
        "state": (previous.state if previous else None) or job.state,
        "title": previous.title if previous else filename,
        "edition": previous.edition if previous else None,
        "source_url": source_url,
        "source_retrieved_at": _utc_now(),
        "source_status": "live",
        "content_sha256": stored.sha256,
        "preserved_url": f"/files/{stored.sha256}.pdf",
        "completeness": "complete",
        "review_status": "auto_pass",
        "license_or_rights": previous.license_or_rights if previous else "public_record",
        "mirrors": mirrors,
        "supersedes": previous.supersedes if previous else None,
        "publisher": previous.publisher if previous else None,
        "published_at": previous.published_at if previous else None,
    }
    if text_sha:
        updates["text_sha256"] = text_sha
    if images_sha:
        updates["images_sha256"] = images_sha
    pages: list[dict] = []
    if kind != "notice":
        try:
            from pipeline.parse import extract_pages
            from pipeline.textstore import text_dir, write_pages

            pages = write_pages(text_dir(files_dir), stored.sha256, extract_pages(data))
        except Exception:
            log.exception("text sidecar failed; preserve still counts")
    if previous is None:
        incoming = Document.from_dict(
            {
                "id": document_id,
                "kind": kind,
                "source_url": source_url,
                "completeness": "complete",
                "airport_lid": updates["airport_lid"],
                "title": updates["title"],
                "edition": updates["edition"],
            }
        )
        prior = prior_work_document(catalog.documents, incoming)
        if prior is not None:
            updates["supersedes"] = prior.id
    if previous and previous.content_sha256 and previous.content_sha256 != stored.sha256:
        note = change_note(
            True,
            content_changed(previous.text_sha256, previous.images_sha256, text_sha, images_sha),
        )
        append_change(
            overlay_dir,
            ChangeEvent(
                id=f"{document_id}-{stored.sha256[:12]}",
                entity_type="document",
                entity_id=document_id,
                detected_at=_utc_now(),
                review_status="pending",
                from_sha256=previous.content_sha256,
                to_sha256=stored.sha256,
                unofficial_note=note,
            ),
        )
    if os.environ.get("APTPLANS_LLM") == "1":
        try:
            from pipeline.ollama import unofficial_note
            from pipeline.parse import extract_text, viable_chunk

            text = extract_text(data)
            if text.strip():
                updates["summary"] = unofficial_note(viable_chunk(text))
        except Exception:
            log.exception("unofficial note failed; preserve still counts")
    write_overlay_update(overlay_dir, document_id, updates)
    try:
        from pipeline.search import upsert_preserved

        upsert_preserved(updates, pages)
    except Exception:
        log.exception("search index failed; preserve still counts")
    _reply(job, "preserved", None)
    log.info(
        "preserved %s sha256=%s airport=%s",
        document_id,
        stored.sha256,
        job.airport_lid,
    )
    return "preserved"


def process_check(
    job: QueueJob,
    overlay_dir: Path,
    catalog_root: Path,
    queue: JobQueue,
) -> str:
    if not job.document_id:
        return "needs_human"
    catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
    document = catalog.documents_by_id.get(job.document_id)
    if document is None:
        log.info("check skipped; unknown document %s", job.document_id)
        return "needs_human"
    outcome = check_document(document)
    apply_outcome(outcome, document, overlay_dir, queue)
    return outcome.status


def _run_claimed_job(
    queue: JobQueue,
    job: QueueJob,
    files_dir: Path,
    overlay_dir: Path,
    catalog_root: Path,
) -> bool:
    """Process a claimed job. True if the public HTML should rebuild after unlock."""
    if job.kind == "check":
        status = process_check(job, overlay_dir, catalog_root, queue)
        queue.complete()
        return status in {"dead", "moved", "live"}
    if job.kind != "fetch":
        log.info("skip unsupported job kind %s", job.kind)
        queue.complete()
        return False
    status = process_fetch(job, files_dir, overlay_dir, catalog_root)
    queue.complete()
    if status != "preserved":
        return False
    catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
    if job.airport_lid:
        log.info(
            "airport %s completeness=%s",
            job.airport_lid,
            completeness_for_airport(catalog, job.airport_lid),
        )
    return True


def _execute_claimed(
    queue: JobQueue,
    job: QueueJob,
    files_dir: Path,
    overlay_dir: Path,
    catalog_root: Path,
) -> bool:
    try:
        return _run_claimed_job(queue, job, files_dir, overlay_dir, catalog_root)
    except Exception:
        if job.attempts >= MAX_ATTEMPTS:
            log.exception(
                "giving up after %s attempts job=%s url=%s",
                job.attempts,
                job.id,
                job.source_url,
            )
            try:
                _reply(job, "needs_human", None)
            except Exception:
                log.exception("intake reply failed after giving up")
            queue.complete()
            return False
        raise JobRetry(job.attempts)


def process_next(
    queue_dir: Path | None = None,
    files_dir: Path | None = None,
    overlay_dir: Path | None = None,
    catalog_root: Path | None = None,
    *,
    pull_intake: bool = False,
) -> bool:
    """Claim and finish one job. True if work ran; False if the queue was idle."""
    queue_dir, files_dir, overlay_dir, catalog_root = _paths(
        queue_dir, files_dir, overlay_dir, catalog_root
    )
    rebuild = False
    with worker_lock(queue_dir):
        queue = JobQueue(queue_dir)
        job = queue.claim()
        if job is not None:
            rebuild = _execute_claimed(queue, job, files_dir, overlay_dir, catalog_root)
            worked = True
        else:
            worked = False

    if worked:
        _rebuild_if_needed(rebuild)
        return True

    if not pull_intake:
        return False

    try:
        incoming = _intake_job_from_github()
    except Exception:
        log.exception("github intake poll failed")
        return False
    if incoming is None:
        return False

    rebuild = False
    with worker_lock(queue_dir):
        queue = JobQueue(queue_dir)
        if incoming.issue_number is not None and queue.has_issue(incoming.issue_number):
            log.info("intake issue %s already queued; skip", incoming.issue_number)
            return False
        queue.enqueue(incoming)
        job = queue.claim()
        if job is None:
            return False
        rebuild = _execute_claimed(queue, job, files_dir, overlay_dir, catalog_root)

    _rebuild_if_needed(rebuild)
    return True


def run_once(
    queue_dir: Path | None = None,
    files_dir: Path | None = None,
    overlay_dir: Path | None = None,
    catalog_root: Path | None = None,
) -> int:
    process_next(
        queue_dir=queue_dir,
        files_dir=files_dir,
        overlay_dir=overlay_dir,
        catalog_root=catalog_root,
        pull_intake=True,
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
