"""Queue-driven static site generation."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from pipeline.refresh import ROOT

log = logging.getLogger("aptplans.site_build")


def enqueue_site_build(
    queue_dir: Path | None = None,
    *,
    airport_lid: str | None = None,
    include_about: bool = False,
) -> bool:
    """Queue one HTML rebuild when none is already pending or active."""
    from pipeline.boot_jobs import enqueue_job
    from pipeline.queue import JobQueue, QueueJob
    from pipeline.status import queue_dir_from_env

    root = queue_dir_from_env(queue_dir)
    queue = JobQueue(root)
    if queue.has_kind("site_build"):
        log.info("site_build already queued")
        return False
    lid = (airport_lid or "").strip().upper() or None
    job = QueueJob(
        kind="site_build",
        document_id=None,
        source_url=None,
        airport_lid=None,
        part_of=lid,
        report_type="about" if include_about and not lid else None,
    )
    queue.enqueue(job)
    if lid:
        log.info("site_build queued lid=%s about=%s", lid, include_about)
    elif include_about:
        log.info("site_build queued about-only")
    else:
        log.info("site_build queued full")
    return True


def run_site_build(
    *,
    airport_lid: str | None = None,
    include_about: bool = False,
) -> str:
    """Run site/build.py. Returns built, unchanged, skipped, or error."""
    site_dir = os.environ.get("APTPLANS_SITE", "").strip()
    if not site_dir:
        log.info("site build skipped; APTPLANS_SITE unset")
        return "skipped"
    builder = ROOT / "site" / "build.py"
    if not builder.is_file():
        log.error("site builder missing: %s", builder)
        return "error"
    cmd = [sys.executable, "-u", str(builder), "--out", site_dir]
    lid = (airport_lid or "").strip().upper()
    if lid:
        cmd.extend(["--lid", lid])
    if include_about:
        cmd.append("--about")
    log.info("site build starting out=%s cmd=%s", site_dir, " ".join(cmd[2:]))
    result = subprocess.run(cmd, cwd=str(ROOT), check=False)
    if result.returncode != 0:
        log.error("site build failed exit=%s", result.returncode)
        return "error"
    log.info("site build finished")
    return "built"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Build static HTML or enqueue a site_build job")
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help="Enqueue site_build on the worker queue instead of building now",
    )
    parser.add_argument("--lid", metavar="LID", help="Partial rebuild for one airport")
    parser.add_argument("--about", action="store_true", help="Include /about/ in a partial rebuild")
    args = parser.parse_args()
    if args.enqueue:
        enqueue_site_build(airport_lid=args.lid, include_about=args.about)
        return 0
    status = run_site_build(airport_lid=args.lid, include_about=args.about)
    return 0 if status in {"built", "unchanged", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
