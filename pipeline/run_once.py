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
    visible_on_site,
)
from catalog.seed import seed_catalog
from catalog.store import append_change, completeness_for_airport, upsert_airport_overlay, write_overlay_update
from pipeline.check import apply_outcome, check_document
from pipeline.explore import confirm_jobs, explore_page, followup_explore_jobs, hub_document_kind, page_title
from pipeline.fetch import fetch_bytes
from pipeline.files import store_bytes
from pipeline.gates import evaluate_payload, filename_from_url, intake_status, sniff_media
from pipeline.github import GitHubIntake, github_from_env
from pipeline.intake import IntakeHint, hint_can_queue, parse_issue_body, resolve_intake
from pipeline.parse import change_note, content_changed, content_fingerprint
from pipeline.stages import review_after_snapshot, review_after_vet
from pipeline.lock import worker_lock
from pipeline.pace import airport_concurrency
from pipeline.outcomes import record_outcome, score_job_signal
from pipeline.pipeline_status import build_public_snapshot, record_discovery, record_job
from pipeline.queue import MAX_ATTEMPTS, JobQueue, JobRetry, QueueJob
from pipeline.boot_jobs import BOOT_JOB_KINDS
from pipeline.refresh import ROOT, overlay_dir_from_env
from pipeline.reject import purge_expired, store_reject
from pipeline.sanitize import redact_html_secrets

log = logging.getLogger("aptplans.job")

# Airport jobs that change published HTML; explore only updates pipeline.json.
SITE_BUILD_TRIGGER_KINDS = frozenset({"fetch", "vet", "check"})


def _observe_job(overlay_dir: Path, job: QueueJob, status: str) -> None:
    """Append a scoring/production outcome. Failures here must not fail the job."""
    try:
        scored = score_job_signal(
            lid=job.airport_lid or "",
            url=job.source_url or "",
            label=job.document_id or "",
        )
        rejected = job.reject_record
        extra = {}
        if isinstance(rejected, dict):
            extra = {
                "reject_sha256": rejected.get("sha256") or None,
                "reject_reason": rejected.get("reason"),
                "reject_expires_at": rejected.get("expires_at"),
                "reject_stored": rejected.get("stored"),
            }
        record_outcome(
            overlay_dir,
            {
                "job_id": job.id,
                "job_kind": job.kind,
                "document_id": job.document_id,
                "lid": job.airport_lid,
                "state": job.state,
                "url": job.source_url,
                "job_status": status,
                "scored": scored or None,
                "source": "worker",
                **extra,
            },
        )
    except Exception:
        log.exception("outcome log failed job=%s", job.id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _keep_failed(
    job: QueueJob,
    files_dir: Path,
    *,
    reason: str,
    data: bytes = b"",
    http_status: int | None = None,
) -> None:
    """Private 90-day copy. Failures here must not fail the job or publish."""
    try:
        job.reject_record = store_reject(
            reason=reason,
            url=job.source_url or "",
            data=data,
            lid=job.airport_lid or "",
            state=job.state or "",
            job_id=job.id,
            job_kind=job.kind,
            http_status=http_status,
            files_dir=files_dir,
        )
    except Exception:
        log.exception("reject store failed job=%s", job.id)


def _paths(
    queue_dir: Path | None,
    files_dir: Path | None,
    overlay_dir: Path | None,
    catalog_root: Path | None,
) -> tuple[Path, Path, Path, Path]:
    return (
        queue_dir or Path(os.environ.get("APTPLANS_QUEUE", ROOT / "data" / "queue")),
        files_dir or Path(os.environ.get("APTPLANS_FILES", ROOT / "data" / "files")),
        overlay_dir or overlay_dir_from_env(),
        catalog_root or (ROOT / "catalog"),
    )


def _llm_generate():
    from pipeline.ollama import generate, llm_calls_enabled

    if not llm_calls_enabled():
        return None
    try:
        return generate
    except Exception:
        log.exception("Ollama unavailable for explore LLM")
        return None


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
    from pipeline.site_build import run_site_build

    run_site_build()


def process_site_build(
    overlay_dir: Path,
    queue_dir: Path,
    catalog_root: Path,
) -> str:
    _refresh_pipeline(overlay_dir, queue_dir, catalog_root)
    from pipeline.site_build import run_site_build

    return run_site_build()


def _run_maintenance_job(job: QueueJob, overlay_dir: Path) -> str:
    from pipeline.boot_jobs import (
        run_budget_enrich,
        run_grant_spend,
        run_ollama_warm,
        run_overlay_refresh,
        run_overview_refresh,
        run_search_sync,
    )

    if job.kind == "overlay_refresh":
        return run_overlay_refresh(overlay_dir)
    if job.kind == "grant_spend":
        return run_grant_spend(overlay_dir)
    if job.kind == "budget_enrich":
        return run_budget_enrich(overlay_dir)
    if job.kind == "overview_refresh":
        return run_overview_refresh(overlay_dir)
    if job.kind == "search_sync":
        return run_search_sync()
    if job.kind == "ollama_warm":
        return run_ollama_warm()
    return "skipped"


def _refresh_pipeline(overlay_dir: Path, queue_dir: Path, catalog_root: Path) -> None:
    try:
        build_public_snapshot(overlay_dir, queue_dir, catalog_root=catalog_root)
    except Exception:
        log.exception("pipeline snapshot failed")


def refresh_public_site(
    overlay_dir: Path,
    queue_dir: Path,
    catalog_root: Path,
) -> None:
    """Write pipeline.json and queue an HTML rebuild for the worker."""
    _refresh_pipeline(overlay_dir, queue_dir, catalog_root)
    try:
        from pipeline.site_build import enqueue_site_build

        enqueue_site_build(queue_dir)
    except Exception:
        log.exception("site build enqueue failed; pipeline snapshot already written")


def process_fetch(
    job: QueueJob,
    files_dir: Path,
    overlay_dir: Path,
    catalog_root: Path,
    queue: JobQueue | None = None,
    data: bytes | None = None,
) -> str:
    if not job.source_url:
        _reply(job, "needs_human", None)
        return "needs_human"
    if data is None:
        try:
            data, _status = fetch_bytes(job.source_url)
        except HTTPError as exc:
            status = "dead" if exc.code in {404, 410} else "needs_human"
            body = b""
            try:
                body = exc.read()
            except Exception:
                body = b""
            log.info("fetch HTTP %s %s -> %s", exc.code, job.source_url, status)
            _keep_failed(job, files_dir, reason=status, data=body, http_status=exc.code)
            _reply(job, status, None)
            return status
        except ValueError as exc:
            log.info("fetch failed %s: %s", job.source_url, exc)
            _keep_failed(job, files_dir, reason="too_large")
            _reply(job, "needs_human", None)
            return "needs_human"
        except (URLError, OSError, TimeoutError, PermissionError) as exc:
            log.info("fetch failed %s: %s", job.source_url, exc)
            _reply(job, "needs_human", None)
            return "needs_human"

    filename = filename_from_url(job.source_url)
    media = sniff_media(data)
    status = intake_status(evaluate_payload(job.source_url, filename, data, allow_html=True))
    if status:
        _keep_failed(job, files_dir, reason=status, data=data)
        _reply(job, status, None)
        return status

    if media == "html":
        data = redact_html_secrets(data.decode("utf-8", "replace")).encode("utf-8")

    suffix = ".pdf" if media == "pdf" else ".html" if media == "html" else ".bin"
    stored = store_bytes(data, files_dir, suffix=suffix)
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
    title = previous.title if previous else filename
    if media == "html" and (not previous or not previous.title):
        title = page_title(data.decode("utf-8", "replace")) or title
    updates = {
        "id": document_id,
        "kind": kind,
        "airport_lid": job.airport_lid or (previous.airport_lid if previous else None),
        "state": (previous.state if previous else None) or job.state,
        "title": title,
        "edition": previous.edition if previous else None,
        "source_url": source_url,
        "source_retrieved_at": _utc_now(),
        "source_status": "live",
        "content_sha256": stored.sha256,
        "preserved_url": f"/files/{stored.sha256}{suffix}",
        "completeness": "complete",
        "review_status": review_after_snapshot(previous.review_status if previous else None),
        "license_or_rights": previous.license_or_rights if previous else "public_record",
        "mirrors": mirrors,
        "supersedes": previous.supersedes if previous else None,
        "publisher": previous.publisher if previous else None,
        "published_at": previous.published_at if previous else None,
        "found_on": job.found_on or (previous.found_on if previous else None) or (
            job.source_url if media == "html" else None
        ),
        "part_of": job.part_of or (previous.part_of if previous else None),
        "media": media,
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

            if media == "html":
                from pipeline.explore import page_excerpt

                pages = write_pages(
                    text_dir(files_dir),
                    stored.sha256,
                    [page_excerpt(data.decode("utf-8", "replace"), 20_000)],
                )
            else:
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
    from pipeline.ollama import llm_calls_enabled, unofficial_note_from_text
    from pipeline.parse import extract_text

    if llm_calls_enabled() and media == "pdf":
        try:
            text = extract_text(data)
            if text.strip():
                updates["summary"] = unofficial_note_from_text(text)
        except Exception:
            log.exception("unofficial note failed; preserve still counts")
    write_overlay_update(overlay_dir, document_id, updates)
    if job.airport_lid and kind in {"master_plan", "alp"}:
        try:
            from pipeline.overviews import upsert_overview_for

            upsert_overview_for(overlay_dir, catalog_root, job.airport_lid)
        except Exception:
            log.exception("overview refresh failed; preserve still counts")
    listed = visible_on_site(
        Document.from_dict(
            {
                "id": document_id,
                "kind": kind,
                "source_url": source_url,
                "completeness": "complete",
                "review_status": updates["review_status"],
            }
        )
    )
    if listed:
        try:
            from pipeline.search import upsert_preserved

            upsert_preserved(updates, pages)
        except Exception:
            log.exception("search index failed; preserve still counts")
    if queue is not None:
        queue.enqueue(
            QueueJob(
                kind="vet",
                document_id=document_id,
                source_url=job.source_url,
                airport_lid=job.airport_lid,
                state=job.state,
                found_on=job.found_on or updates.get("found_on"),
            )
        )
    if media == "html" and queue is not None and job.source_url:
        result = explore_page(
            data.decode("utf-8", "replace"),
            job.source_url,
            generate_fn=_llm_generate(),
            overlay_dir=overlay_dir,
        )
        for child in confirm_jobs(
            result,
            airport_lid=job.airport_lid,
            state=job.state,
        ):
            if child.source_url and child.source_url != job.source_url:
                queue.enqueue(child)
                log.info("explore queued %s from %s", child.source_url, job.source_url)
        first_hop = not job.found_on or job.found_on.rstrip("/") == job.source_url.rstrip("/")
        if first_hop:
            for child in followup_explore_jobs(
                result,
                airport_lid=job.airport_lid,
                state=job.state,
            ):
                queue.enqueue(child)
                log.info("explore follow-up %s from %s", child.source_url, job.source_url)
    _reply(job, "preserved", None)
    log.info(
        "preserved %s sha256=%s airport=%s media=%s review=%s",
        document_id,
        stored.sha256,
        job.airport_lid,
        media,
        updates["review_status"],
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


def process_explore(
    job: QueueJob,
    files_dir: Path,
    overlay_dir: Path,
    catalog_root: Path,
    queue: JobQueue,
) -> str:
    if not job.source_url:
        return "needs_human"
    try:
        data, _status = fetch_bytes(job.source_url)
    except HTTPError as exc:
        status = "dead" if exc.code in {404, 410} else "needs_human"
        body = b""
        try:
            body = exc.read()
        except Exception:
            body = b""
        log.info("explore fetch HTTP %s %s -> %s", exc.code, job.source_url, status)
        _keep_failed(job, files_dir, reason=status, data=body, http_status=exc.code)
        return status
    except ValueError as exc:
        log.info("explore fetch failed %s: %s", job.source_url, exc)
        _keep_failed(job, files_dir, reason="too_large")
        return "needs_human"
    except (URLError, OSError, TimeoutError, PermissionError) as exc:
        log.info("explore fetch failed %s: %s", job.source_url, exc)
        return "needs_human"
    media = sniff_media(data)
    if media == "pdf":
        return process_fetch(
            job, files_dir, overlay_dir, catalog_root, queue=queue, data=data
        )
    if media != "html":
        _keep_failed(job, files_dir, reason="not_plan", data=data)
        return "not_plan"
    result = explore_page(
        data.decode("utf-8", "replace"),
        job.source_url,
        generate_fn=_llm_generate(),
        overlay_dir=overlay_dir,
    )
    if not job.suggested_kind:
        job.suggested_kind = hub_document_kind(result)
    if not job.document_id and job.airport_lid:
        job.document_id = f"{job.airport_lid.lower()}-site"
    return process_fetch(
        job, files_dir, overlay_dir, catalog_root, queue=queue, data=data
    )


def process_vet(
    job: QueueJob,
    files_dir: Path,
    overlay_dir: Path,
    catalog_root: Path,
) -> str:
    """Classify a snapshot. Does not fetch. Does not publish unless the record already was."""
    if not job.document_id:
        return "needs_human"
    catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
    document = catalog.documents_by_id.get(job.document_id)
    if document is None or not document.content_sha256:
        return "pending"
    from pipeline.ollama import llm_calls_enabled

    if document.review_status == "published":
        return "published"
    if not llm_calls_enabled():
        log.info("vet deferred; LLM disabled document=%s", job.document_id)
        return "pending"
    stored = files_dir / f"{document.content_sha256}.pdf"
    if not stored.is_file():
        stored = files_dir / f"{document.content_sha256}.html"
    if not stored.is_file():
        return "pending"
    data = stored.read_bytes()
    try:
        from pipeline.ollama import generate
        from pipeline.parse import extract_text, viable_chunk
        from pipeline.queries import verify_candidate, verify_finance

        excerpt = (
            viable_chunk(extract_text(data))
            if data.startswith(b"%PDF")
            else viable_chunk(data.decode("utf-8", "replace"))
        )
        airport = catalog.airports_by_lid.get(document.airport_lid or "")
        state = catalog.states_by_code.get(document.state or "")
        scored = verify_candidate(
            lid=document.airport_lid or "",
            name=airport.name if airport is not None else "",
            url=document.source_url,
            excerpt=excerpt,
            generate_fn=generate,
        )
        finance = verify_finance(
            url=document.source_url,
            excerpt=excerpt,
            generate_fn=generate,
            lid=document.airport_lid or "",
            name=airport.name if airport is not None else "",
            state=state.code if state is not None else (document.state or ""),
        )
    except Exception:
        log.exception("vet failed document=%s", job.document_id)
        return "pending"
    kind = str(scored.get("kind") or "")
    review = review_after_vet(
        official_plan=bool(scored.get("official_plan")),
        same_airport=bool(scored.get("same_airport")),
        kind=kind,
    )
    if kind == "not_plan":
        _keep_failed(job, files_dir, reason="not_plan", data=data)
    updates: dict = {"review_status": review}
    if finance.get("finance_kind"):
        updates["finance_kind"] = finance["finance_kind"]
        updates["finance_scope"] = finance.get("scope")
        updates["finance_reason"] = finance.get("reason") or None
        updates["finance_verified_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            from pipeline.classifications import record_classification

            record_classification(
                overlay_dir,
                evaluation="finance_verify",
                input_id=document.id,
                category=str(finance.get("finance_kind") or "other"),
                classifier="llm",
                reason=str(finance.get("reason") or ""),
            )
        except Exception:
            log.exception("finance classification audit failed document=%s", document.id)
    write_overlay_update(overlay_dir, document.id, updates)
    if visible_on_site(document.overlay(updates)):
        try:
            from pipeline.search import upsert_preserved

            upsert_preserved({**document.to_dict(), **updates}, [])
        except Exception:
            log.exception("search index failed after vet; review still written")
    log.info("vet %s review=%s", document.id, review)
    return review


def _run_claimed_job(
    queue: JobQueue,
    job: QueueJob,
    files_dir: Path,
    overlay_dir: Path,
    catalog_root: Path,
) -> bool:
    """Process a claimed job. True if the public HTML should rebuild after complete."""
    if job.kind == "check":
        status = process_check(job, overlay_dir, catalog_root, queue)
        _observe_job(overlay_dir, job, status)
        try:
            record_job(overlay_dir, job, status)
        except Exception:
            log.exception("pipeline status failed job=%s", job.id)
        return status in {"dead", "moved", "live"}
    if job.kind == "site_build":
        status = process_site_build(overlay_dir, queue.root, catalog_root)
        _observe_job(overlay_dir, job, status)
        try:
            record_job(overlay_dir, job, status)
        except Exception:
            log.exception("pipeline status failed job=%s", job.id)
        return False
    if job.kind in BOOT_JOB_KINDS:
        status = _run_maintenance_job(job, overlay_dir)
        _observe_job(overlay_dir, job, status)
        try:
            record_job(overlay_dir, job, status)
        except Exception:
            log.exception("pipeline status failed job=%s", job.id)
        return False
    if job.kind == "explore":
        status = process_explore(job, files_dir, overlay_dir, catalog_root, queue)
        _observe_job(overlay_dir, job, status)
        try:
            record_job(overlay_dir, job, status)
        except Exception:
            log.exception("pipeline status failed job=%s", job.id)
        return False
    if job.kind == "vet":
        status = process_vet(job, files_dir, overlay_dir, catalog_root)
        _observe_job(overlay_dir, job, status)
        try:
            record_job(overlay_dir, job, status)
        except Exception:
            log.exception("pipeline status failed job=%s", job.id)
        return status in {"auto_pass", "published"}
    if job.kind != "fetch":
        log.info("skip unsupported job kind %s", job.kind)
        _observe_job(overlay_dir, job, "skipped")
        return False
    status = process_fetch(job, files_dir, overlay_dir, catalog_root, queue=queue)
    _observe_job(overlay_dir, job, status)
    try:
        record_job(overlay_dir, job, status)
    except Exception:
        log.exception("pipeline status failed job=%s", job.id)
    if status != "preserved":
        return False
    catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
    if job.airport_lid:
        log.info(
            "airport %s completeness=%s",
            job.airport_lid,
            completeness_for_airport(catalog, job.airport_lid),
        )
    return False


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
                _observe_job(overlay_dir, job, "needs_human")
                record_job(overlay_dir, job, "needs_human")
                _reply(job, "needs_human", None)
            except Exception:
                log.exception("intake reply failed after giving up")
            return False
        raise JobRetry(job.attempts)


def _claim_next_job(
    queue_dir: Path,
    files_dir: Path,
) -> QueueJob | None:
    with worker_lock(queue_dir):
        try:
            purged = purge_expired(files_dir=files_dir)
            if purged.get("dropped"):
                log.info(
                    "reject purge dropped=%s removed_files=%s kept=%s",
                    purged.get("dropped"),
                    purged.get("removed_files"),
                    purged.get("kept"),
                )
        except Exception:
            log.exception("reject purge failed")
        return JobQueue(queue_dir).claim(airport_limit=airport_concurrency())


def _finish_job(
    queue_dir: Path,
    job: QueueJob,
    files_dir: Path,
    overlay_dir: Path,
    catalog_root: Path,
) -> None:
    _execute_claimed(
        JobQueue(queue_dir),
        job,
        files_dir,
        overlay_dir,
        catalog_root,
    )
    with worker_lock(queue_dir):
        JobQueue(queue_dir).complete(job)
    if job.kind == "site_build" or job.kind in BOOT_JOB_KINDS:
        if job.kind == "overlay_refresh":
            from pipeline.boot_jobs import enqueue_post_overlay_refresh

            try:
                followups = enqueue_post_overlay_refresh(queue_dir)
                if followups:
                    log.info("post-overlay jobs enqueued: %s", ",".join(followups))
            except Exception:
                log.exception("post-overlay enqueue failed")
        return
    if job.kind in SITE_BUILD_TRIGGER_KINDS:
        refresh_public_site(overlay_dir, queue_dir, catalog_root)
    else:
        _refresh_pipeline(overlay_dir, queue_dir, catalog_root)


def process_next(
    queue_dir: Path | None = None,
    files_dir: Path | None = None,
    overlay_dir: Path | None = None,
    catalog_root: Path | None = None,
    *,
    pull_intake: bool = False,
    pull_discovery: bool = False,
) -> bool:
    """Claim and finish one job. True if work ran; False if the queue was idle."""
    queue_dir, files_dir, overlay_dir, catalog_root = _paths(
        queue_dir, files_dir, overlay_dir, catalog_root
    )
    job = _claim_next_job(queue_dir, files_dir)
    if job is not None:
        _finish_job(queue_dir, job, files_dir, overlay_dir, catalog_root)
        return True

    if pull_discovery:
        try:
            from pipeline.discover_overlay import discover_next_airports

            result = discover_next_airports(overlay_dir, queue_dir)
            if result.get("airports"):
                record_discovery(overlay_dir, list(result.get("airports") or []))
                _refresh_pipeline(overlay_dir, queue_dir, catalog_root)
            if result.get("explore_jobs") or result.get("fetch_jobs"):
                job = _claim_next_job(queue_dir, files_dir)
                if job is not None:
                    _finish_job(queue_dir, job, files_dir, overlay_dir, catalog_root)
                    return True
        except Exception:
            log.exception("discovery enqueue failed")
        return False

    if not pull_intake:
        return False

    try:
        incoming = _intake_job_from_github()
    except Exception:
        log.exception("github intake poll failed")
        return False
    if incoming is None:
        return False

    with worker_lock(queue_dir):
        queue = JobQueue(queue_dir)
        if incoming.issue_number is not None and queue.has_issue(incoming.issue_number):
            log.info("intake issue %s already queued; skip", incoming.issue_number)
            return False
        queue.enqueue(incoming)

    job = _claim_next_job(queue_dir, files_dir)
    if job is None:
        return False
    _finish_job(queue_dir, job, files_dir, overlay_dir, catalog_root)
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
