"""Fetch NASR, NPIAS, OurAirports home pages, and AIP grant histories. CI must not call this live."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import time
from pathlib import Path

from catalog.airports import (
    NASR_LISTING_URL,
    current_apt_csv_url,
    merge_airports,
    parse_nasr_apt_zip,
    preserve_admitted,
)
from catalog.npias import NPIAS_SOURCE, parse_appendix_a_bytes
from catalog.ourairports import (
    OURAIRPORTS_CSV_URL,
    apply_ourairports,
    parse_ourairports_csv,
)
from catalog.store import load_airports_overlay, load_overlay, write_airports_overlay
from pipeline.fetch import fetch_bytes, post_json
from pipeline.lock import worker_lock
from pipeline.refresh import (
    PAUSE_SECONDS,
    ROOT,
    overlay_airports_path,
    overlay_dir_from_env,
    should_refresh,
)
from pipeline.refresh_grants import maybe_refresh_grants

log = logging.getLogger("aptplans.airports")

_EDITION_RE = re.compile(r"NPIAS-(\d{4}-\d{4})")


def npias_edition_from_url(url: str) -> str | None:
    match = _EDITION_RE.search(url)
    return match.group(1) if match else None


def refresh_airports(
    overlay_dir: Path,
    *,
    fetch=fetch_bytes,
    sleep=time.sleep,
    pause_seconds: float = PAUSE_SECONDS,
) -> int:
    listing, _ = fetch(NASR_LISTING_URL, timeout=180)
    zip_url = current_apt_csv_url(listing.decode("utf-8", errors="replace"))
    sleep(pause_seconds)
    nasr_bytes, _ = fetch(zip_url, timeout=180)
    sleep(pause_seconds)
    npias_bytes, _ = fetch(NPIAS_SOURCE, timeout=180)
    nasr = parse_nasr_apt_zip(nasr_bytes)
    npias = parse_appendix_a_bytes(npias_bytes)
    snapshot = merge_airports(
        nasr,
        npias,
        npias_edition=npias_edition_from_url(NPIAS_SOURCE),
    )
    sleep(pause_seconds)
    oa_rows: dict = {}
    try:
        oa_bytes, _ = fetch(OURAIRPORTS_CSV_URL, timeout=180)
        oa_rows = parse_ourairports_csv(oa_bytes)
    except (OSError, PermissionError, TimeoutError, ValueError, csv.Error) as exc:
        log.warning("OurAirports skipped (%s); writing NASR/NPIAS without home pages", exc)
    existing = load_airports_overlay(overlay_dir)
    document_lids = {
        str(row.get("airport_lid") or "")
        for row in load_overlay(overlay_dir).values()
        if row.get("airport_lid")
    }
    airports = preserve_admitted(snapshot, existing, document_lids)
    airports = apply_ourairports(airports, oa_rows)
    if not airports:
        raise ValueError("NASR/NPIAS snapshot produced no airports")
    write_airports_overlay(overlay_dir, airports)
    log.info("wrote %s airports to %s", len(airports), overlay_airports_path(overlay_dir))
    return len(airports)


def maybe_refresh(
    overlay_dir: Path,
    *,
    force: bool = False,
    fetch=fetch_bytes,
    sleep=time.sleep,
    post_json=None,
) -> int | None:
    path = overlay_airports_path(overlay_dir)
    if not force and not should_refresh(path):
        log.info("airport overlay is current: %s", path)
        airports_count = None
    else:
        airports_count = refresh_airports(overlay_dir, fetch=fetch, sleep=sleep)
    maybe_refresh_grants(
        overlay_dir, force=force, fetch=fetch, sleep=sleep, post_json=post_json
    )
    from pipeline.overviews import refresh_overviews

    refresh_overviews(overlay_dir, force=force)
    return airports_count


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Fetch NASR, NPIAS, OurAirports home pages, and AIP grant histories into the catalog overlay"
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    overlay = overlay_dir_from_env()
    queue = Path(os.environ.get("APTPLANS_QUEUE", ROOT / "data" / "queue"))
    with worker_lock(queue):
        maybe_refresh(overlay, force=args.force, post_json=post_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
