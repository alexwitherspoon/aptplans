"""Queue-driven static site generation."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from pipeline.refresh import ROOT
from pipeline.site_scope import (
    apply_scope_to_job,
    merge_scopes,
    pending_site_build_scope,
    scope_cli_flags,
    scope_from_job,
)
from pipeline.queue import JobQueue, QueueJob
from pipeline.status import queue_dir_from_env

log = logging.getLogger("aptplans.site_build")


def _scope_log(scope) -> str:
    if scope is None:
        return "full"
    parts = []
    if scope.airport_lids:
        parts.append(f"lids={','.join(sorted(scope.airport_lids))}")
    if scope.state_codes:
        parts.append(f"states={','.join(sorted(scope.state_codes))}")
    if scope.document_ids:
        parts.append(f"docs={len(scope.document_ids)}")
    for flag in (
        "include_about",
        "include_index",
        "include_airports_index",
        "include_states_index",
        "include_search_page",
        "include_feeds_index",
        "include_data",
        "include_global_feeds",
    ):
        if getattr(scope, flag):
            parts.append(flag.removeprefix("include_"))
    return "partial(" + " ".join(parts) + ")" if parts else "partial"


def enqueue_site_build(
    queue_dir: Path | None = None,
    scope=None,
    *,
    parent_job_id: str | None = None,
) -> bool:
    """Queue one HTML rebuild, merging scope into an existing pending job when possible."""
    root = queue_dir_from_env(queue_dir)
    queue = JobQueue(root)
    if parent_job_id is not None:
        job = QueueJob(
            kind="site_build",
            document_id=None,
            source_url=None,
            airport_lid=None,
            dedupe_key="maintenance:site_build",
            retry_class="continuous",
            parent_job_id=parent_job_id,
        )
        apply_scope_to_job(job, scope)
        queued = queue.defer(parent_job_id, job)
        if queued:
            log.info("site_build deferred %s", _scope_log(scope))
        return queued
    existing_scope, existing = pending_site_build_scope(queue)
    if existing is not None:
        merged = merge_scopes(existing_scope, scope)
        if existing_scope is None:
            log.info("site_build already queued (full)")
            return False
        if merged == existing_scope:
            log.info("site_build already queued with equal or wider scope")
            return False
        apply_scope_to_job(existing, merged)
        queue.update_pending(existing)
        log.info("site_build scope widened to %s", _scope_log(merged))
        return True
    active = queue.jobs(state="active", kind="site_build")
    dedupe_key = (
        f"maintenance:site_build:after:{active[0].id}"
        if active
        else "maintenance:site_build"
    )
    job = QueueJob(
        kind="site_build",
        document_id=None,
        source_url=None,
        airport_lid=None,
        dedupe_key=dedupe_key,
        retry_class="continuous",
        parent_job_id=parent_job_id,
    )
    apply_scope_to_job(job, scope)
    queue.enqueue(job)
    log.info("site_build queued %s", _scope_log(scope))
    return True


def run_site_build(scope=None) -> str:
    """Run site/build.py. Returns built, unchanged, skipped, or error."""
    site_dir = os.environ.get("APTPLANS_SITE", "").strip()
    if not site_dir:
        log.info("site build skipped; APTPLANS_SITE unset")
        return "skipped"
    builder = ROOT / "site" / "build.py"
    if not builder.is_file():
        log.error("site builder missing: %s", builder)
        return "error"
    if os.environ.get("APTPLANS_DOMAIN_STORE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        releases_root = os.environ.get("APTPLANS_RELEASES", "").strip()
        if not releases_root:
            log.error("generation release build requires APTPLANS_RELEASES")
            return "error"
        from catalog.seed import seed_catalog_snapshot
        from pipeline.domain_store import DomainStore
        from pipeline.public_files import reconcile_public_files
        from pipeline.release_coordinator import ReleaseCoordinator
        from pipeline.status import queue_dir_from_env

        ledger_root = queue_dir_from_env()
        domain = DomainStore(ledger_root)
        domain_snapshot = domain.snapshot()
        snapshot = seed_catalog_snapshot(ROOT / "catalog", domain_snapshot)
        from pipeline.queue import _utc_now

        audit_cutoff = _utc_now()

        def build_release(staged_site: Path, staged_files: Path) -> None:
            environment = dict(os.environ)
            environment["APTPLANS_DOMAIN_GENERATION"] = snapshot.generation_id
            environment["APTPLANS_AUDIT_CUTOFF"] = audit_cutoff
            result = subprocess.run(
                [
                    sys.executable,
                    "-u",
                    str(builder),
                    "--out",
                    str(staged_site),
                    "--full",
                ],
                cwd=str(ROOT),
                env=environment,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"site builder failed exit={result.returncode}")
            reconcile_public_files(snapshot.catalog, public_dir=staged_files)

        coordinator = ReleaseCoordinator(ledger_root, Path(releases_root))
        coordinator.recover()
        previous_id = coordinator.releases.current_generation_id()
        if previous_id == snapshot.generation_id:
            log.info("generation release unchanged generation=%s", snapshot.generation_id)
            return "unchanged"
        previous = (
            seed_catalog_snapshot(ROOT / "catalog", domain.snapshot(previous_id))
            if previous_id
            else None
        )
        try:
            coordinator.stage(
                snapshot,
                build_release,
                metadata={
                    "domain_generation_id": snapshot.generation_id,
                    "domain_committed_at": snapshot.committed_at,
                    "audit_cutoff": audit_cutoff,
                },
            )
            coordinator.activate(snapshot, previous=previous)
        except Exception:
            log.exception("generation release failed generation=%s", snapshot.generation_id)
            return "error"
        log.info("generation release active generation=%s", snapshot.generation_id)
        return "built"
    cmd = [sys.executable, "-u", str(builder), "--out", site_dir]
    if scope is None:
        cmd.append("--full")
    else:
        for lid in sorted(scope.airport_lids):
            cmd.extend(["--lid", lid])
        for code in sorted(scope.state_codes):
            cmd.extend(["--state", code])
        for doc_id in sorted(scope.document_ids):
            cmd.extend(["--document", doc_id])
        if scope.include_about:
            cmd.append("--about")
        if scope.include_index:
            cmd.append("--index")
        if scope.include_airports_index:
            cmd.append("--airports-index")
        if scope.include_states_index:
            cmd.append("--states-index")
        if scope.include_search_page:
            cmd.append("--search")
        if scope.include_feeds_index:
            cmd.append("--feeds")
        if scope.include_data:
            cmd.append("--data")
        if scope.include_global_feeds:
            cmd.append("--global-feeds")
    log.info("site build starting out=%s cmd=%s", site_dir, " ".join(cmd[2:]))
    result = subprocess.run(cmd, cwd=str(ROOT), check=False)
    if result.returncode != 0:
        log.error("site build failed exit=%s", result.returncode)
        return "error"
    log.info("site build finished")
    return "built"


def process_site_build_job(job: QueueJob) -> str:
    from catalog.seed import seed_catalog

    catalog = seed_catalog(ROOT / "catalog")
    scope = scope_from_job(job, catalog)
    return run_site_build(scope)


def add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--full", action="store_true", help="Rebuild the entire site tree")
    parser.add_argument(
        "--lid",
        action="append",
        default=[],
        metavar="LID",
        help="Regenerate one airport page and related state/documents/feeds",
    )
    parser.add_argument("--state", action="append", default=[], metavar="CODE", help="Regenerate a state hub")
    parser.add_argument("--document", action="append", default=[], metavar="ID", help="Regenerate one document page")
    parser.add_argument("--about", action="store_true", help="Regenerate /about/")
    parser.add_argument("--index", action="store_true", help="Regenerate the home page")
    parser.add_argument("--airports-index", action="store_true", help="Regenerate /airports/")
    parser.add_argument("--states-index", action="store_true", help="Regenerate /states/")
    parser.add_argument("--search", action="store_true", help="Regenerate /search/")
    parser.add_argument("--feeds", action="store_true", help="Regenerate /feeds/")
    parser.add_argument("--data", action="store_true", help="Regenerate /data/search.json and catalog dumps")
    parser.add_argument("--global-feeds", action="store_true", help="Regenerate /feeds/all.xml and /feeds/laws.xml")


def main() -> int:
    from catalog.seed import seed_catalog

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Build static HTML or enqueue a site_build job")
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help="Enqueue site_build on the worker queue instead of building now",
    )
    add_scope_arguments(parser)
    args = parser.parse_args()
    catalog = seed_catalog(ROOT / "catalog")
    scope = scope_cli_flags(args, catalog)
    if args.enqueue:
        enqueue_site_build(scope=scope)
        return 0
    status = run_site_build(scope)
    return 0 if status in {"built", "unchanged", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
