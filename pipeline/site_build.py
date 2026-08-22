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


def enqueue_site_build(queue_dir: Path | None = None) -> bool:
    """Queue one HTML rebuild when none is already pending or active."""
    from pipeline.boot_jobs import enqueue_job

    return enqueue_job(queue_dir, "site_build")


def run_site_build() -> str:
    """Run site/build.py. Returns built, unchanged, skipped, or error."""
    site_dir = os.environ.get("APTPLANS_SITE", "").strip()
    if not site_dir:
        log.info("site build skipped; APTPLANS_SITE unset")
        return "skipped"
    builder = ROOT / "site" / "build.py"
    if not builder.is_file():
        log.error("site builder missing: %s", builder)
        return "error"
    log.info("site build starting out=%s", site_dir)
    result = subprocess.run(
        [sys.executable, "-u", str(builder), "--out", site_dir],
        cwd=str(ROOT),
        check=False,
    )
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
    args = parser.parse_args()
    if args.enqueue:
        enqueue_site_build()
        return 0
    status = run_site_build()
    return 0 if status in {"built", "unchanged", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
