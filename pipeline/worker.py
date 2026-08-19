"""Idle Compose process the systemd timer execs into. Does not serve HTTP."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from pipeline.fetch import fetch_bytes, post_json
from pipeline.refresh import ROOT, overlays_need_fetch
from pipeline.refresh_airports import maybe_refresh

log = logging.getLogger("aptplans.worker")

BOOT_PAUSE_SECONDS = 5.0


def cold_start_overlays(
    overlay_dir: Path | None = None,
    *,
    fetch=fetch_bytes,
    sleep=time.sleep,
    pause_before: float = BOOT_PAUSE_SECONDS,
    post_json=None,
) -> bool:
    """Fetch NASR, NPIAS, and AIP grants if overlays are missing or stale. Never force."""
    if os.environ.get("APTPLANS_REFRESH_AIRPORTS") != "1":
        log.info("FAA overlay fetch off (APTPLANS_REFRESH_AIRPORTS unset)")
        return False
    overlay = overlay_dir or Path(
        os.environ.get("APTPLANS_CATALOG_OVERLAY", ROOT / "data" / "catalog")
    )
    if not overlays_need_fetch(overlay):
        log.info("FAA overlays present for this month; skip fetch")
        return False
    log.info(
        "FAA overlays missing or stale; wait %.0fs, then fetch one request at a time",
        pause_before,
    )
    if pause_before:
        sleep(pause_before)
    maybe_refresh(overlay, fetch=fetch, sleep=sleep, post_json=post_json)
    return True


def _rebuild_site() -> None:
    site_dir = os.environ.get("APTPLANS_SITE", "").strip()
    if not site_dir:
        return
    builder = ROOT / "site" / "build.py"
    subprocess.run(
        [sys.executable, str(builder), "--out", site_dir],
        check=False,
        cwd=str(ROOT),
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log.info(
        "worker idle host=%s model=%s",
        os.environ.get("OLLAMA_HOST", ""),
        os.environ.get("OLLAMA_MODEL", ""),
    )
    try:
        if cold_start_overlays(post_json=post_json):
            _rebuild_site()
    except Exception:
        log.exception("FAA overlay fetch failed; worker stays up")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
